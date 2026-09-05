"""OpenAI 兼容 LLM 调用。

任意兼容端点均可：DeepSeek / 硅基流动 / 通义 / OpenAI 等，
只需在 .env 里改 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL。

v2 优化：超时 + 指数退避重试（网络抖动自动恢复，不把错误甩给用户）。
"""
import asyncio
import json

import httpx
from openai import AsyncOpenAI

from .config import config
from .log import logger

_client: AsyncOpenAI | None = None


def _build_http_client() -> httpx.AsyncClient | None:
    """构建 LLM 请求的底层 HTTP 客户端。

    默认返回 None（openai SDK 自建，会读系统代理环境变量）。
    若设置了 LLM_PROXY 环境变量，则用指定代理；设为「off/direct/none」时
    强制直连（trust_env=False），规避本机残留的失效本地代理
    （如 127.0.0.1:57622 这类随会话漂移的临时代理端口）导致的外网请求全挂。
    """
    proxy = (config.llm_proxy or "").strip().lower()
    if not proxy:
        return None
    if proxy in ("off", "direct", "none", "no"):
        return httpx.AsyncClient(trust_env=False, timeout=config.llm_timeout)
    return httpx.AsyncClient(proxy=proxy, trust_env=False, timeout=config.llm_timeout)

# 重试策略
_MAX_RETRIES = 2                 # 最多重试 2 次（共 3 次尝试）
_RETRY_BASE_SEC = 1.5            # 首次退避 1.5s

# 可安全重试的异常类型（网络/超时/连接类/5xx/限流）
_RETRYABLE = (TimeoutError,)


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试。"""
    if isinstance(exc, _RETRYABLE):
        return True
    # openai.APIConnectionError：连接层失败（DNS/握手/连接被重置等），值得重试
    try:
        from openai import APIConnectionError

        if isinstance(exc, APIConnectionError):
            return True
    except Exception:
        pass
    # openai.APIStatusError：429 / 5xx 可重试
    try:
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if isinstance(status, int) and status >= 500:
            return True
        if status == 429:  # 限流——退避重试
            return True
    except Exception:
        pass
    return False


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not config.llm_api_key:
            raise RuntimeError("未配置 LLM_API_KEY（请先复制 .env.example 为 .env 并填写）")
        _client = AsyncOpenAI(
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            timeout=config.llm_timeout,
            max_retries=0,  # 自己控制重试，避免 SDK 与这里双重退避
            http_client=_build_http_client(),
        )
    return _client


def get_perception_client() -> AsyncOpenAI:
    """感知层专用 client：使用 LLM_PERCEPTION_* 独立配置（小模型/独立端点）。

    复用主 client 缓存的思路，但用独立 key 缓存；未配置独立端点时退回主 client。
    感知层是高频轻量调用，独立 client 能隔离超时/限流，避免影响主对话。
    """
    global _client
    if config.llm_perception_model or config.llm_perception_base_url:
        # 有独立配置 → 独立 client（缓存于模块级，key 变化需 reload 重置）
        pc = getattr(get_perception_client, "_client", None)
        if pc is None:
            base = config.llm_perception_base_url or config.llm_base_url
            # key 回退顺序：显式 LLM_PERCEPTION_API_KEY → 同端点 IMAGE_API_KEY
            # （硅基流动常见配法：生图 key 与 chat 小模型同 key）→ 主 LLM_API_KEY。
            key = (
                config.llm_perception_api_key
                or config.image_api_key
                or config.llm_api_key
            )
            if not key:
                raise RuntimeError("未配置感知层 API key")
            pc = AsyncOpenAI(
                base_url=base,
                api_key=key,
                timeout=config.llm_perception_timeout,
                max_retries=0,
                http_client=_build_http_client(),
            )
            get_perception_client._client = pc
        return pc
    return get_client()


def _record_usage(channel: str, model: str, usage, prompt_text: str, completion_text: str) -> None:
    """记录一次调用的 token 用量（D5 成本面板）。端点未返回 usage 时按字符估算（CJK≈1 token/1.5 字）。"""
    try:
        from .userdb import log_usage

        pt = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        ct = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        estimated = not (pt or ct)
        if estimated:
            pt = max(1, round(len(prompt_text) / 1.5))
            ct = max(1, round(len(completion_text) / 1.5))
        from .current_user import current_user_id
        from .persona_profiles import active_user_id

        log_usage(current_user_id.get() or active_user_id(), channel, model or "", pt, ct, estimated)
    except Exception:
        pass


async def chat(
    messages: list[dict],
    *,
    mock: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    perception: bool = False,
    model: str | None = None,
) -> str:
    """非流式整条回复。mock=True 时返回占位回复，便于无 API key 调试。

    perception=True 时走感知层独立小模型（LLM_PERCEPTION_* 配置），
    用于高频轻量的语义感知，降低延迟/成本；未配置独立模型时行为与普通 chat 一致。
    model 显式指定时优先（D5 强模型路由）。

    失败自动重试（指数退避），全部失败抛异常（调用方兜底）。
    """
    if mock:
        # mock 回显最后一条**用户**消息（若末尾是 system 指令，别把 system 内容当回复）
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return f"[模拟回复] 收到啦：{last[:30]}……(￣▽￣)"
    client = get_perception_client() if perception else get_client()
    effective_model = model or (
        (config.llm_perception_model or config.llm_model)
        if perception
        else config.llm_model
    )
    prompt_text = "".join(str(m.get("content") or "") for m in messages)
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.chat.completions.create(
                model=effective_model,
                messages=messages,
                temperature=config.llm_temperature if temperature is None else temperature,
                max_tokens=config.llm_max_tokens if max_tokens is None else max_tokens,
            )
            text = resp.choices[0].message.content or ""
            _record_usage("perception" if perception else "chat", effective_model,
                          getattr(resp, "usage", None), prompt_text, text)
            return text
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt >= _MAX_RETRIES:
                break
            wait = _RETRY_BASE_SEC * (2**attempt)
            logger.warning("[LLM] 第{}次失败（{}），{:.1f}s 后重试", attempt + 1, type(e).__name__, wait)
            await asyncio.sleep(wait)
    raise last_exc  # 全部失败，交给调用方兜底


async def chat_native(
    messages: list[dict],
    tools: list[dict] | None = None,
    *,
    mock: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> tuple[str, list[dict]]:
    """原生函数调用——返回 (回复文本, tool_calls 列表)。

    tool_calls 元素: {"name": str, "arguments": dict}
    若空列表则表示 LLM 直接回复了文本（最终回复）。
    """
    if mock:
        return await chat(messages, mock=mock), []
    client = get_client()
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            kwargs: dict = {"model": config.llm_model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if temperature is not None:
                kwargs["temperature"] = temperature
            else:
                kwargs["temperature"] = config.llm_temperature
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = config.llm_max_tokens
            resp = await client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            text = msg.content or ""
            _record_usage("tool", config.llm_model, getattr(resp, "usage", None),
                          "".join(str(m.get("content") or "") for m in messages), text)
            calls: list[dict] = []
            for tc in (msg.tool_calls or []):
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        logger.warning("[LLM] 工具 {} 参数不是对象，已按空对象处理", tc.function.name)
                        args = {}
                except Exception as je:
                    logger.warning("[LLM] 工具 {} 的参数 JSON 解析失败：{}", tc.function.name, je)
                    args = {}
                calls.append({"name": tc.function.name, "arguments": args})
            return text, calls
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt >= _MAX_RETRIES:
                break
            wait = _RETRY_BASE_SEC * (2**attempt)
            logger.warning("[LLM] 原生调用第{}次失败（{}），{:.1f}s 后重试",
                          attempt + 1, type(e).__name__, wait)
            await asyncio.sleep(wait)
    # 若 tools 参数导致 API 错误（不支持/未知参数），降级回文本 + 空 tool_calls
    if tools and last_exc:
        msg = str(last_exc).lower()
        # 收窄降级判定：只有明确表示"不支持 tools/未知参数"才回退文本模式，
        # 避免把网络/鉴权等错误误判为不支持函数调用
        if any(k in msg for k in ("not supported", "unsupported", "unknown parameter",
                                  "unexpected parameter", "does not support", "tools.*not")):
            logger.warning("[LLM] 原生工具调用不受支持，降级回文本模式: {}", str(last_exc)[:100])
            text = await chat(messages, mock=mock)
            return text, []
    raise last_exc  # type: ignore[union-attr]


async def chat_stream(
    messages: list[dict],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
):
    """流式回复：逐 chunk 产出文本片段（打字机效果）。连接前失败直接抛出，调用方兜底。

    model 显式指定时优先（D5 强模型路由）。
    重试策略：仅在**尚未产出任何片段**时允许重试（连接失败/首块前断开）；
    已经 yield 过内容后再失败，直接抛出——否则重试会从头重新产出已发送的
    片段，前端出现重复文本。
    """
    if getattr(config, "llm_stream_disable", False):
        # 留一个逃生开关：某些端点不支持 stream 时退回整句
        yield await chat(messages, temperature=temperature, max_tokens=max_tokens, model=model)
        return
    client = get_client()
    effective_model = model or config.llm_model
    prompt_text = "".join(str(m.get("content") or "") for m in messages)
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        produced = False
        produced_text = ""
        usage = None
        try:
            stream = await client.chat.completions.create(
                model=effective_model,
                messages=messages,
                temperature=config.llm_temperature if temperature is None else temperature,
                max_tokens=config.llm_max_tokens if max_tokens is None else max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    produced = True
                    produced_text += piece
                    yield piece
            _record_usage("reply", effective_model, usage, prompt_text, produced_text)
            return
        except Exception as e:
            last_exc = e
            if produced or not _is_retryable(e) or attempt >= _MAX_RETRIES:
                break  # 已产出过内容/不可重试/重试耗尽：终止（避免前端收到重复片段）
            wait = _RETRY_BASE_SEC * (2**attempt)
            logger.warning("[LLM] 流式第{}次失败（{}），{:.1f}s 后重试",
                          attempt + 1, type(e).__name__, wait)
            await asyncio.sleep(wait)
    raise last_exc  # type: ignore[misc]


_ADDRESS_EXTRACT_PROMPT = (
    "你是称呼提取器。用户在给对话助手设置自己希望被称呼的名字。"
    "只有用户在明确告诉你怎么称呼他（如『叫我某某』『你可以叫我某某』）时才提取；"
    "如果只是普通聊天、或不是在设置称呼，就输出空。"
    "提取时只取一个最合适的称呼，只输出这一个词本身，不要输出任何其他文字、符号、引号或解释。\n"
    "例子：\n"
    "『就叫我以实玛利吧』→ 以实玛利\n"
    "『叫我良秀也行』→ 良秀\n"
    "『我叫小明』→ 小明（仅当在接受称呼场景下）\n"
    "『你其实是AI对吧』→ \n"
    "『你好』→ \n"
    "『我平时喜欢下雨』→ "
)


async def extract_address(text: str) -> str | None:
    """用 LLM 从用户消息中精确提取称呼；无明确称呼时返回 None。"""
    resp = await chat(
        [
            {"role": "system", "content": _ADDRESS_EXTRACT_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
        max_tokens=20,
    )
    name = resp.strip().strip("「」『』\"'“”《》 ")
    # 校验：过长/含换行/含标点的结果视为提取失败，避免把整句当称呼
    if not name or len(name) > 12:
        return None
    if any(ch in name for ch in "\r\n\t，。！？、；：()（）"):
        return None
    return name
