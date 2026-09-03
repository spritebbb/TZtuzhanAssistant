"""图像生成：用 SiliconFlow 文生图，把用户的描述画成一张图。

- generate(prompt) → 下载图片到 data/imgs/，返回本地文件路径；失败返回 None
- 通过 IMAGE_API_KEY / IMAGE_MODEL / IMAGE_BASE_URL 配置
- 不配置 key 则生图关闭
"""
import hashlib
import urllib.request
import json
from pathlib import Path

from .config import config
from .log import logger


def enabled() -> bool:
    return bool(config.image_api_key)


# ---- 生图错误分类（给用户可操作的提示）----

class ImageGenError(Exception):
    """生图失败，携带面向用户的说明文字。"""

    def __init__(self, user_msg: str, *, log_msg: str = ""):
        super().__init__(user_msg)
        self.user_msg = user_msg
        self.log_msg = log_msg


def _raise_typed(exc: Exception) -> ImageGenError:
    """把任意异常映射为带用户提示的错误。"""
    msg = str(exc)
    low = msg.lower()
    log_msg = msg[:200]
    # urllib HTTPError 有 .code；优先用它
    code = getattr(exc, "code", None)
    if code is None and isinstance(exc, urllib.error.HTTPError):
        code = exc.code
    if isinstance(code, int):
        if code in (401, 403):
            return ImageGenError(
                "生图的密钥好像不对（IMAGE_API_KEY 无效），让管理员检查一下配置吧～",
                log_msg=log_msg,
            )
        if code in (402,):
            return ImageGenError(
                "生图余额不够啦……充值一下就能继续画了（SiliconFlow 账户余额不足）",
                log_msg=log_msg,
            )
        if code == 429:
            return ImageGenError(
                "画图的人有点多，被限流了，等一小会儿再试一次吧～",
                log_msg=log_msg,
            )
        if 400 <= code < 500:
            return ImageGenError(
                "这段描述生图服务好像理解不了，换个更直白的说法试试？",
                log_msg=log_msg,
            )
        if code >= 500:
            return ImageGenError(
                "生图服务暂时掉线了（服务端出问题），过一会儿再让我画呀～",
                log_msg=log_msg,
            )
    # 无状态码：按消息文本兜底
    if "401" in msg or "unauthorized" in low or "invalid api key" in low:
        return ImageGenError(
            "生图的密钥好像不对（IMAGE_API_KEY 无效），让管理员检查一下配置吧～",
            log_msg=log_msg,
        )
    if "402" in msg or "insufficient" in low or "balance" in low or "quota" in low:
        return ImageGenError(
            "生图余额不够啦……充值一下就能继续画了（SiliconFlow 账户余额不足）",
            log_msg=log_msg,
        )
    if "429" in msg or "rate" in low or "too many" in low:
        return ImageGenError(
            "画图的人有点多，被限流了，等一小会儿再试一次吧～",
            log_msg=log_msg,
        )
    if "400" in msg or "bad request" in low or "invalid" in low:
        return ImageGenError(
            "这段描述生图服务好像理解不了，换个更直白的说法试试？",
            log_msg=log_msg,
        )
    # 默认：网络/服务端
    return ImageGenError(
        "生图服务暂时掉线了（可能是网络或服务端问题），过一会儿再让我画呀～",
        log_msg=log_msg,
    )


def _imgs_dir() -> Path:
    d = config.data_dir / "imgs"
    d.mkdir(parents=True, exist_ok=True)
    return d


_GEN_MAX_BYTES = 20 * 1024 * 1024  # 生图结果下载上限 20MB（防异常大图撑爆内存）
_GEN_TIMEOUT = 150                # 生图请求超时（秒）：SiliconFlow 生图偶尔偏慢，放宽避免误超时
_GEN_MAX_RETRIES = 2              # 网络类失败最多重试 2 次（4xx/5xx 有明确原因，不重试）
_GEN_RETRY_BASE_SEC = 3.0         # 首次重试等待秒数


def _is_retryable(exc: Exception) -> bool:
    """判断生图失败是否值得重试：仅网络/超时/连接类（服务端可能只是抖一下）。"""
    import socket
    import urllib.error

    # 明确的服务端状态码：不重试（配置/限流/余额/模型理解问题，重试也白搭）
    if isinstance(exc, urllib.error.HTTPError):
        return False
    # 网络/超时/连接类：值得重试
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, (socket.timeout, ConnectionError, urllib.error.URLError)):
        return True
    low = str(exc).lower()
    if any(k in low for k in ("timed out", "timeout", "connection", "reset", "refused", "eof", "read operation")):
        return True
    return False


def _download(url: str, timeout: int = _GEN_TIMEOUT) -> bytes:
    if url.startswith("data:"):
        import base64

        data = base64.b64decode(url.split(",", 1)[1])
        if len(data) > _GEN_MAX_BYTES:
            raise ValueError(f"图片超过 {_GEN_MAX_BYTES // 1024 // 1024}MB，拒绝")
        return data
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(_GEN_MAX_BYTES + 1)
    if len(data) > _GEN_MAX_BYTES:
        raise ValueError(f"图片超过 {_GEN_MAX_BYTES // 1024 // 1024}MB，拒绝")
    return data


def _guess_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def generate_sync(prompt: str, size: str = "1024x1024") -> str | None:
    """同步生成一张图并下载到本地；返回本地路径，失败返回 None。

    失败时把分类错误（ImageGenError）记录到日志，调用方可用
    `imagegen.last_error()` 拿到面向用户的可操作提示。
    """
    global _last_error
    if not enabled():
        logger.warning("[生图] 未配置 IMAGE_API_KEY，跳过")
        _last_error = ImageGenError(
            "还没配置生图密钥（IMAGE_API_KEY），想让我画画的话先在 .env 里填上 SiliconFlow 的 key 吧～",
            log_msg="IMAGE_API_KEY 未配置",
        )
        return None
    if not prompt.strip():
        _last_error = ImageGenError("描述是空的，跟我说说想画什么呀？")
        return None
    # 统一风格：日系二次元动漫风（默认），让对话生图和 /画 输出一致的二次元质感
    styled = (
        "日系二次元动漫风格，精致美型，画面干净通透，"
        f"{prompt}，色彩明快，光影柔和，构图讲究"
    )

    # 整个「发起生图 + 下载落盘」封装成可重试的一步：网络类失败重试，服务端
    # 明确错误（4xx/5xx/无图/无url）直接抛 ImageGenError 由外层处理。
    def _request_once() -> str:
        import urllib.error

        payload = json.dumps(
            {
                "model": config.image_model,
                "prompt": styled,
                "image_size": size,
                "batch_size": 1,
                "num_inference_steps": 20,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{config.image_base_url.rstrip('/')}/images/generations",
            data=payload,
            headers={
                "Authorization": f"Bearer {config.image_api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_GEN_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 带状态码的服务端错误：转成带用户提示的异常，不重试
            raise _raise_typed(e)
        images = data.get("images") or []
        if not images:
            raise ImageGenError(
                "生图服务返回了空结果，再试一次或换个描述吧～",
                log_msg=f"无图片: {str(data)[:200]}",
            )
        url = images[0].get("url") or ""
        if not url:
            raise ImageGenError(
                "图生成了但没拿到图片地址，再试一次吧～",
                log_msg=f"无url: {str(images[0])[:200]}",
            )
        # 下载落盘（用 prompt 哈希命名，避免重复）
        raw = _download(url)
        digest = hashlib.md5(raw).hexdigest()[:16]
        path = _imgs_dir() / f"{digest}{_guess_ext(raw)}"
        path.write_bytes(raw)
        return str(path)

    try:
        last_exc: Exception | None = None
        for attempt in range(_GEN_MAX_RETRIES + 1):
            try:
                path = _request_once()
                _last_error = None
                return path
            except ImageGenError:
                raise
            except Exception as e:
                last_exc = e
                if not _is_retryable(e) or attempt >= _GEN_MAX_RETRIES:
                    break
                wait = _GEN_RETRY_BASE_SEC * (2**attempt)
                logger.warning("[生图] 第{}次失败（{}），{:.0f}s 后重试", attempt + 1, type(e).__name__, wait)
                import time as _time

                _time.sleep(wait)
        typed = _raise_typed(last_exc)
        _last_error = typed
        logger.error("[生图] 生成失败：{} | {}", prompt[:50], typed.log_msg)
        return None
    except ImageGenError as e:
        _last_error = e
        logger.error("[生图] 生成失败：{} | {}", prompt[:50], e.log_msg)
        return None


_last_error: ImageGenError | None = None


def last_error() -> str:
    """最近一次生图失败的面向用户提示；没有失败返回空串。"""
    return _last_error.user_msg if _last_error else ""


async def generate(prompt: str, size: str = "1024x1024") -> str | None:
    """异步入口（生成是 CPU/网络阻塞，放线程池避免卡事件循环）。"""
    import asyncio

    return await asyncio.to_thread(generate_sync, prompt, size)
