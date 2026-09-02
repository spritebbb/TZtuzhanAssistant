# -*- coding: utf-8 -*-
"""工具调用循环：让 LLM 通过**原生 Function Calling**（OpenAI 兼容 tools 参数）调用工具，
结果注入下一轮，最多 N 轮直到不再需要工具。

设计原则：
- 优先原生 Function Calling：模型原生返回 tool_calls，可靠稳定（对标 Harness）
- 兼容回退：若端点不支持 tools 参数，降级为 ```tool {json}``` 文本协议解析
- 任何失败都静默降级为普通回复，绝不阻塞对话
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from ..core.log import logger
from .base import ToolRegistry

# 工具循环轮次上限
MAX_LOOPS = 3

# 文本协议正则（回退模式用）
_TOOL_BLOCK_RE = re.compile(r"```tool[ \t]*(?:\n| )([\s\S]*?)```", re.MULTILINE)
_STRAY_TOOL_RE = re.compile(r"```tool[^\n]*\n?")


def parse_tool_blocks(text: str) -> tuple[str, list[dict]]:
    """从 LLM 输出中提取 ```tool``` 代码块（回退模式），返回 (清理后文本, 工具调用列表)。"""
    calls: list[dict] = []
    for match in _TOOL_BLOCK_RE.finditer(text):
        block = match.group(1).strip()
        if not block:
            continue
        try:
            obj = json.loads(block)
        except Exception:
            # 全角字符容错
            try:
                fixed = (
                    block.replace("：", ":")
                    .replace("，", ",")
                    .replace("｛", "{")
                    .replace("｝", "}")
                    .replace("＂", '"')
                    .replace("“", '"')
                    .replace("”", '"')
                    .replace("‘", "'")
                    .replace("’", "'")
                )
                obj = json.loads(fixed)
            except Exception:
                logger.warning("[工具循环] 无法解析工具代码块: {}", block[:80])
                continue
        if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
            args = obj.get("args")
            if not isinstance(args, dict):
                args = {}
            calls.append({"name": obj["tool"], "arguments": args})
    clean_text = _TOOL_BLOCK_RE.sub("", text).strip()
    clean_text = _STRAY_TOOL_RE.sub("", clean_text).strip()
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)
    return clean_text, calls


def _extract_last_user(work: list[dict]) -> str:
    """从消息历史中取最后一条用户消息（作工具参数兜底）。"""
    for m in reversed(work):
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].strip():
            return m["content"]
    return ""


def _fill_missing_args(call: dict, fallback: str, tool_schema: dict | None) -> dict:
    """工具被调用但参数缺失/为空时，用对话上下文兜底填充。

    只填充 schema 里标记 required 的字符串参数（优先 content/query/text/prompt 等主参）。
    对 number 类型参数尝试提取 fallback 中的第一个数字。
    """
    args = dict(call.get("arguments") or {})
    if args or not fallback or not tool_schema:
        return args
    # input_schema 本身即 parameters 结构；也可能被包了一层 {"parameters": {...}}
    params = tool_schema if "properties" in tool_schema else (tool_schema.get("parameters") or {})
    props = params.get("properties") or {}
    required = params.get("required") or []
    # 优先顺序：content > query > text > prompt > 第一个必填字符串参数
    preferred = ("content", "query", "text", "prompt")
    candidates = [k for k in required if k in props] or list(props.keys())
    ordered = [k for k in preferred if k in candidates] + [k for k in candidates if k not in preferred]
    for key in ordered:
        p = props.get(key) or {}
        if p.get("type") == "string":
            args[key] = fallback
            return args
    # 无字符串参数时，尝试提取 number 类型（如 amount 金额）
    for key in ordered:
        p = props.get(key) or {}
        if p.get("type") == "number":
            nums = re.findall(r"(\d+(?:\.\d+)?)", fallback)
            if nums:
                args[key] = float(nums[0])
                return args
    return args


# ---- 参数清洗层：deepseek 系模型常把整句用户指令塞进单个参数，这里剥离指令前缀/提取真正的值 ----

# 路径字符类：仅 ASCII（\w 会匹配中文，把"帮我找"误当路径）
_ASCII_PATH_CHARS = r"A-Za-z0-9_.\\/-"
_PATH_RE = re.compile(rf"[{_ASCII_PATH_CHARS}]+\.(?:py|md|txt|json|js|ts|html|css|yml|yaml|toml|ini|log|csv|bat|ps1|sh|java|c|cpp|h|go|rs|vue|jsx|tsx|png|jpg|jpeg|gif)\b")
_URL_RE = re.compile(r"https?://[^\s，。；;'\"]+")

# 常见指令前缀（按长度降序匹配，避免"帮我看看"吃掉"看看"后的内容）
_INSTRUCTION_PREFIXES = [
    "帮我看看", "帮我看一下", "帮我查一下", "帮我搜索", "帮我搜一下", "帮我搜搜",
    "帮我记录", "帮我记一个待办", "帮我记个待办", "帮我记待办", "帮我记一下",
    "帮我记一个", "帮我记", "帮我找一下", "帮我找",
    "帮我写个待办", "帮我添加待办", "帮我创建一个待办", "帮我创建待办",
    "帮我打开", "帮我读取", "帮我读一下", "帮我翻译", "帮我总结",
    "请搜索", "请查一下", "请看看", "请读取", "请记住",
    "搜索一下", "搜一下", "查一下", "搜搜", "搜索", "查询",
    "看看", "读取", "读一下", "打开", "记住", "记录",
    "记一个待办", "记个待办", "记待办", "添加待办", "创建待办",
    "有什么", "有哪些", "是什么", "是啥", "列一下", "列举",
]
# "用" 前缀：LLM 在"用 X 工具做 Y"时把整句塞进参数，需要剥离
_INSTRUCTION_PREFIXES.extend([
    "用子代理", "用子代理工具", "用代理工具",
    "用 run_python", "用 run_command", "用 python 执行",
    "用 glob 工具", "用 grep 工具", "用 read_file 工具",
    "用 skill_load", "用 skill_search", "用 skill 工具",
    "用 currency_convert", "用汇率工具",
    "用 agent_run", "用 agent_fanout",
    "用记忆工具", "用待办工具", "用搜索工具",
    "帮我执行", "帮我运行", "执行一下", "运行一下",
])
_INSTRUCTION_PREFIXES = sorted(set(_INSTRUCTION_PREFIXES), key=len, reverse=True)

_TAIL_WORDS = ["吗", "吧", "呢", "啊", "哦", "？", "?", "的", "好", "哈"]

# "用 X 工具做 Y"：剥掉"用 <工具名> [工具] [来做...]"；支持"帮我用/请用/麻烦用"等前导
_USE_TOOL_RE = re.compile(r"^(?:帮我|请|麻烦|给我)?\s*用\s*[\w_]+(?:\s*工具)?\s*(?:来|帮我|给我|请|帮)?\s*")

# 动作动词（剥掉指令前缀后残留的动词，如"算一下/找一下/加载/读取"）
_ACTION_VERBS = [
    "算一下", "计算一下", "算算", "算",
    "找一下", "找", "查找", "搜索一下",
    "加载", "调用", "读取", "读一下", "查看",
    "换算", "换一下", "转换", "计算",
    "列出", "列举", "写一下", "生成", "把",
    "执行", "运行",
]

# glob pattern 归一化："backend/core/ 下的所有 .py 文件" → "backend/core/**/*.py"
_GLOB_DIR_RE = re.compile(rf"^([{_ASCII_PATH_CHARS}]+)/?\s*(?:下的|里(?:面)?的|中(?:的)?|内)?\s*(?:所有|全部)?\s*(.+?文件)$")
_EXT_HINT = {"py": "*.py", "js": "*.js", "ts": "*.ts", "json": "*.json", "md": "*.md",
             "txt": "*.txt", "html": "*.html", "css": "*.css", "vue": "*.vue",
             "pyc": "*.pyc", "csv": "*.csv", "log": "*.log", "png": "*.png", "jpg": "*.jpg"}


def _nl_to_python(value: str) -> str | None:
    """把常见中文自然语言算式转成 Python 代码；无法识别返回 None。"""
    v = value.strip()
    # "1 到 100 的和/总和/累加" → sum(range(1, 101))
    m = re.match(
        r"^(\d+(?:\.\d+)?)\s*(?:到|至|~)\s*(\d+(?:\.\d+)?)\s*(?:的)?(?:和|总和|累加|求和|加起来)$", v
    )
    if m:
        a = int(float(m.group(1)))
        b = int(float(m.group(2)))
        return f"print(sum(range({a}, {b + 1})))"
    # "15 乘 23" / "15 乘以 23" / "15 × 23" / "15 * 23"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:乘|乘以|×|\*)\s*(\d+(?:\.\d+)?)$", v)
    if m:
        return f"print({m.group(1)} * {m.group(2)})"
    # "100 加 23" / "100 减 5" / "100 除以 4"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(加|加上|减|减去|除以)\s*(\d+(?:\.\d+)?)$", v)
    if m:
        op = {"加": "+", "加上": "+", "减": "-", "减去": "-", "除以": "/"}[m.group(2)]
        return f"print({m.group(1)} {op} {m.group(3)})"
    # 纯算式 "1+1" / "17*23" / "(5+3)*2"
    if re.fullmatch(r"[\d+\-*/().\s]+", v) and any(op in v for op in "+-*/"):
        return f"print({v})"
    return None


def _strip_instruction(value: str, *, trim_tail: bool = False) -> str:
    """剥离指令前缀（可选地去掉句尾语气词），留下真正的参数内容。

    trim_tail=True 时也只在确认这条值确实以"指令前缀/动作动词"开头后才去句尾
    语气词——避免把合法正文内容（如记忆内容"我家的狗很乖的"）的"的"误删。
    content/code/command 等正文类参数必须原样保留，不要传 trim_tail。
    """
    v = value.strip()
    # 去首尾引号
    if len(v) >= 2 and v[0] in "\"'「『“" and v[-1] in "\"'」』”":
        v = v[1:-1].strip()
    # 去"用 X 工具做 Y"结构
    use_matched = bool(_USE_TOOL_RE.match(v))
    v = _USE_TOOL_RE.sub("", v).strip(" ：:，,。.、")
    matched = use_matched
    # 去指令前缀
    for w in sorted(_INSTRUCTION_PREFIXES, key=len, reverse=True):
        if v.startswith(w):
            v = v[len(w):].strip(" ：:，,。.、")
            matched = True
            break
    # 去残留动作动词（如"算一下 1 到 100 的和" → "1 到 100 的和"）
    if not matched:
        for w in _ACTION_VERBS:
            if v.startswith(w):
                v = v[len(w):].strip(" ：:，,。.、")
                matched = True
                break
    # 去句尾语气词：仅当确实剥过指令且调用方允许时（正文类参数不传 trim_tail）
    if matched and trim_tail:
        changed = True
        while changed and v:
            changed = False
            for w in _TAIL_WORDS:
                if v.endswith(w) and len(v) > len(w):
                    v = v[:-len(w)].rstrip(" ：:，,。.、")
                    changed = True
                    break
    return v.strip()


def _clean_args(call: dict, user_text: str) -> dict:
    """对 LLM 传入的参数做清洗：提取路径/URL/剥离指令前缀。"""
    args = dict(call.get("arguments") or {})
    if not args:
        return args
    name = call.get("name", "")
    for key, value in list(args.items()):
        if not isinstance(value, str):
            continue
        v = value.strip()
        if not v:
            continue
        # 路径类参数（path/pattern 或文件名含扩展名）
        if key in ("path", "pattern", "cwd"):
            # glob 的 pattern 含通配符时不做路径提取（避免把 `**/*.py` 误切成 `/py`）
            has_glob = any(ch in v for ch in "*?{}[]")
            if key != "pattern" or not has_glob:
                m = _PATH_RE.search(v)
                if m:
                    args[key] = m.group(0)
                    continue
            cleaned = _strip_instruction(v)
            if key != "pattern" or not has_glob:
                m2 = _PATH_RE.search(cleaned)
                if m2:
                    args[key] = m2.group(0)
                    continue
            # glob pattern 归一化："backend/core/ 下的所有 .py 文件" → "backend/core/**/*.py"
            if key == "pattern":
                gm = _GLOB_DIR_RE.match(cleaned)
                if gm:
                    dir_part = gm.group(1).rstrip("/\\")
                    file_desc = gm.group(2)
                    # 从描述中提取扩展名如 ".py", "py"
                    ext_match = re.search(r"\.(\w+)\b", file_desc)
                    if ext_match:
                        ext = ext_match.group(1)
                        pattern = _EXT_HINT.get(ext, f"*.{ext}")
                        args[key] = f"{dir_part}/**/{pattern}"
                        continue
                    # 从描述中找关键词如 "python" → "*.py"
                    if "python" in file_desc or "py" in file_desc:
                        args[key] = f"{dir_part}/**/*.py"
                        continue
                    if "文本" in file_desc or "txt" in file_desc:
                        args[key] = f"{dir_part}/**/*.txt"
                        continue
                    args[key] = f"{dir_part}/**/*"
                    continue
            args[key] = cleaned
        # URL 类参数
        elif key == "url":
            m = _URL_RE.search(v)
            if m:
                args[key] = m.group(0)
            else:
                args[key] = _strip_instruction(v)
        # 内容/查询类参数：剥离指令前缀
        elif key in ("content", "query", "text", "prompt", "tasks_json", "code", "command"):
            # content/code/command 是正文/代码类，禁止任何句尾语气词裁剪；
            # query/text/prompt/tasks_json 仅在确认带指令前缀时才 trim_tail
            cleaned = _strip_instruction(v, trim_tail=key in ("query", "text", "prompt", "tasks_json"))
            # code：自然语言算式 → Python 代码（如 "1 到 100 的和" → sum(range(...))）
            if key == "code":
                py = _nl_to_python(cleaned)
                if py:
                    args[key] = py
                else:
                    args[key] = cleaned
            elif key == "command":
                # 去尾部的"命令/这个命令"等描述词
                for tail in ("命令", "这个命令", "这条命令", "的命令"):
                    if cleaned.endswith(tail):
                        cleaned = cleaned[: -len(tail)].strip(" ：:，,。.、")
                        break
                args[key] = cleaned
            else:
                args[key] = cleaned
    return args


async def _execute_calls(calls: list[dict], fallback: str = "") -> str:
    """并行执行工具调用，返回结果文本块。"""
    import asyncio

    specs = {t.name: t for t in ToolRegistry.list()}

    def _prepare(c: dict) -> dict:
        filled = _fill_missing_args(c, fallback, getattr(specs.get(c["name"]), "input_schema", None))
        return _clean_args({"name": c["name"], "arguments": filled}, fallback)

    results = await asyncio.gather(*[
        ToolRegistry.execute(c["name"], _prepare(c))
        for c in calls
    ])
    parts = []
    for i, (c, r) in enumerate(zip(calls, results)):
        body = r.output if r.ok else (r.error or "调用失败")
        parts.append(f"[工具结果 {i + 1}/{len(results)} - {c['name']}]\n{body}")
    return "\n\n".join(parts)


def _tool_hint_text() -> str:
    """回退模式的文本工具提示。"""
    tools = [t for t in ToolRegistry.list() if t.name not in ("run_python", "run_command")]
    lines = [f"- {t.name}：{t.description}" for t in tools]
    return "可用工具：\n" + "\n".join(lines) + (
        "\n\n如需工具，请用 ```tool``` 代码块：\n```tool\n{\"tool\": \"工具名\", \"args\": {...}}\n```"
    )


async def run_tool_loop(
    messages: list[dict],
    call_llm: Callable,
    *,
    max_loops: int = MAX_LOOPS,
    mock: bool = False,
    final_instruction: list[dict] | None = None,
    call_native: Callable | None = None,
) -> str:
    """执行完整工具循环，返回最终 LLM 文本。

    Args:
        messages: 当前对话消息（最后一条是 user；含人格/记忆/上下文等注入）
        call_llm: 文本模式回调，接收 messages 返回 LLM 输出字符串
        max_loops: 最大循环轮次
        mock: 测试模式
        final_instruction: 无工具调用时可选追加的 system 消息
        call_native: 原生函数调用回调，接收 (messages, tools) 返回 (text, tool_calls)
                     若不提供则走文本协议回退模式

    Returns:
        最终文本（不含工具代码块）
    """
    if mock:
        return await call_llm(messages)

    tools = ToolRegistry.openai_tools()
    work = list(messages)

    # 原生模式优先
    if call_native is not None:
        return await _run_native(
            work, call_native, tools,
            max_loops=max_loops, final_instruction=final_instruction,
        )

    # 回退：文本协议模式
    return await _run_text(
        work, call_llm,
        max_loops=max_loops, final_instruction=final_instruction,
    )


async def _run_native(
    work: list[dict],
    call_native: Callable,
    tools: list[dict],
    *,
    max_loops: int,
    final_instruction: list[dict] | None,
) -> str:
    """原生 Function Calling 循环。"""
    fallback = _extract_last_user(work)
    # 首轮注入一条强制提醒：记忆/画像里提到的旧事不替代工具调用
    _REINFORCE = (
        "⚠️ 注意：用户要求你「记录/记住/待办/搜索/查询/回忆」时，必须调用对应的工具"
        "（todo_create / memory_add / web_search / memory_search），"
        "不能依赖你的内部记忆或对话上下文。"
        "即使你觉得已经知道或已经记住了，也要先把工具调了再说。"
        "工具结果是唯一可靠的记录来源。你上下文里的记忆提示仅供参考，不替代工具操作。"
    )
    # 强化指令插入到 user 消息之前（确保 user 是发给模型的最后一条，避免被系统指令盖过）
    if work and work[-1].get("role") == "user":
        work.insert(-1, {"role": "system", "content": _REINFORCE})
    else:
        work.append({"role": "system", "content": _REINFORCE})
    loop_count = 0
    while loop_count < max_loops:
        loop_count += 1
        try:
            text, calls = await call_native(work, tools)
        except Exception as e:
            logger.warning("[工具循环] call_native 异常: {}", e)
            # 真实错误直接透传，不用误导性文案掩盖（key 失效/网络问题用户需要知道）
            return f"（处理失败：{type(e).__name__}: {str(e)[:200]}）"
        logger.info("[工具循环] 第{}轮: {} 个工具调用 {}", loop_count, len(calls),
                    [c["name"] for c in calls])
        if not calls:
            return text or "（模型未返回内容，请重试）"

        # 为每个调用分配稳定 id（带轮次前缀，避免多轮历史中 id 重复，
        # DeepSeek/vLLM 等严格校验端点会因重复 tool_call_id 报 400）
        for i, c in enumerate(calls):
            c["_id"] = f"call_{loop_count}_{i}"

        # 注入 assistant 的 tool_calls（每个调用独立 id）
        work.append({
            "role": "assistant",
            "content": text or None,
            "tool_calls": [
                {"id": c["_id"], "type": "function",
                 "function": {"name": c["name"],
                              "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False)}}
                for c in calls
            ],
        })
        # 每个工具结果一条独立 tool 消息（id 与 tool_calls 一一对应）
        specs = {t.name: t for t in ToolRegistry.list()}
        for c in calls:
            filled = _fill_missing_args(c, fallback, getattr(specs.get(c["name"]), "input_schema", None))
            filled = _clean_args({"name": c["name"], "arguments": filled}, fallback)
            logger.info("[工具循环] 调用 {} 参数={}", c["name"], json.dumps(filled, ensure_ascii=False)[:300])
            r = await ToolRegistry.execute(c["name"], filled)
            body = r.output if r.ok else (r.error or "调用失败")
            work.append({
                "role": "tool",
                "tool_call_id": c["_id"],
                "content": f"[{c['name']}] {body}",
            })
        if loop_count >= max_loops:
            break

    # 循环用尽：无 tools 再生成一次最终回复
    if final_instruction:
        work.extend(list(final_instruction))
    text, calls = await call_native(work, None)
    return text or "（模型未返回内容，请重试）"


async def _run_text(
    work: list[dict],
    call_llm: Callable,
    *,
    max_loops: int,
    final_instruction: list[dict] | None,
) -> str:
    """文本协议回退循环。"""
    work = list(work)
    work.append({"role": "system", "content": _tool_hint_text()})
    fallback = _extract_last_user(work)
    loop_count = 0
    while loop_count < max_loops:
        loop_count += 1
        raw = await call_llm(work)
        clean, calls = parse_tool_blocks(raw)
        if not calls:
            if final_instruction:
                work.extend(list(final_instruction))
                final_raw = await call_llm(work)
                final_clean, _ = parse_tool_blocks(final_raw)
                return final_clean or "（我先记一下，回头跟你说）"
            return clean or "（我先记一下，回头跟你说）"

        result_block = await _execute_calls(calls, fallback)
        work.append({"role": "assistant", "content": clean or "（我查一下）"})
        work.append({
            "role": "system",
            "content": "你刚调用的工具返回了这些结果（可能有误，只作为参考）：\n"
            + result_block
            + "\n请根据结果组织你的回复（保持干脆利落、口语化，别报告腔、别列清单）。"
            "如果还需要更多信息，可以再调用工具；否则直接给出最终回复。",
        })

    if final_instruction:
        work.extend(list(final_instruction))
    raw = await call_llm(work)
    clean, _ = parse_tool_blocks(raw)
    return clean or raw
