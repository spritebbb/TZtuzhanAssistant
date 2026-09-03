"""本地 embedding 层：sentence-transformers 加载 BGE 系列模型（本机 CPU 推理）。

设计：
- 默认模型 BAAI/bge-m3（多语言，8192 token，1024 维），首次使用自动下载到本机缓存
- 下载/加载失败自动回退到 bge-small-zh-v1.5（约 100MB，中文友好），再失败回退到
  纯字符二元组哈希向量（零依赖，保证系统永不因 embedding 故障而中断）
- 全局懒加载单例 + 线程锁（embedding 可能被 asyncio.to_thread 的工作线程并发调用）
- embed(text) 同步接口：返回 list[float]；失败返回 None（调用方回退）

环境变量：
- MEMORY_EMBED_MODEL：覆盖默认模型名
- MEMORY_EMBED_FORCE：'1' 时跳过本地模型，强制用回退哈希向量（调试用）
"""
import hashlib
import re
import threading
import time

from ..config import config
from ..log import logger

# 默认模型（按优先级）
_PRIMARY_MODEL = "BAAI/bge-m3"
_FALLBACK_MODEL = "BAAI/bge-small-zh-v1.5"
_HASH_DIM = 768  # 哈希回退向量维度

_lock = threading.RLock()
_model = None          # SentenceTransformer 实例
_model_name: str | None = None
_emb_cache: dict[str, tuple[str, list[float]]] = {}  # text -> (mode, vec)
_EMB_CACHE_MAX = 4000
_last_load_fail_ts = 0.0
_LOAD_COOLDOWN = 600.0  # 模型加载失败后冷却 10 分钟，避免每条文本都重试下载


def _strip(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def enabled() -> bool:
    """本地 embedding 是否可用（能加载模型或有哈希回退，恒为 True）。"""
    return True


def mode() -> str:
    """当前 embedding 生效模式：'model:<名称>'（真模型）或 'hash'（无语义哈希回退）。"""
    _load_model()
    return f"model:{_model_name}" if _model is not None else "hash"


def is_loaded() -> bool:
    """模型是否已加载成功（不触发加载，供状态查询使用）。"""
    return _model is not None


def current_model() -> str | None:
    """当前已加载的模型名（未加载返回 None，不触发加载）。"""
    return _model_name


def _load_model():
    """加载本地模型（带回退链），返回当前生效的模型名。"""
    global _model, _model_name, _last_load_fail_ts
    if not _lock.acquire(blocking=False):
        # 另一个线程正在加载/预热模型：不排队等待（下载可能数分钟），
        # 本次调用直接走哈希回退，避免把调用方线程卡在模型下载上
        return None
    try:
        if _model is not None:
            return _model_name
        now = time.monotonic()
        if now - _last_load_fail_ts < _LOAD_COOLDOWN:
            return None  # 冷却期内不重试，走哈希回退
        forced = config.memory_embed_force
        candidates: list[str] = []
        if not forced:
            candidates.append(config.memory_embed_model or _PRIMARY_MODEL)
            if candidates[0] != _FALLBACK_MODEL:
                candidates.append(_FALLBACK_MODEL)
        for name in candidates:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info("[记忆] 加载 embedding 模型：{}（首次会下载，约 1~2GB）", name)
                m = SentenceTransformer(name, device="cpu")
                _model = m
                _model_name = name
                return name
            except Exception as e:
                logger.warning("[记忆] 模型 {} 加载失败：{}，尝试下一个", name, str(e)[:120])
        _model_name = None  # 全部失败 → 走哈希回退
        _last_load_fail_ts = time.monotonic()
        logger.warning(
            "[记忆] embedding 模型加载失败，{} 分钟内不再重试，降级为哈希向量"
            "（对话不受影响，语义检索质量下降）", int(_LOAD_COOLDOWN // 60)
        )
        return None
    finally:
        _lock.release()


def warmup() -> str:
    """服务启动预热：尝试加载本地 embedding 模型（下载可能很慢，调用方应放后台线程）。

    返回生效模式：'model:<名称>' 或 'hash'。失败/冷却期不会抛异常，
    通过返回值与日志明确告知降级状态，避免静默退化。
    """
    try:
        m = mode()
    except Exception as e:
        logger.warning("[记忆] embedding 预热失败，降级为哈希向量：{}", str(e)[:120])
        return "hash"
    if _model is None:
        logger.warning(
            "[记忆] embedding 预热未就绪（模型不可用/冷却中），已降级为哈希向量；"
            "对话不受影响，语义检索质量下降"
        )
    else:
        logger.info("[记忆] embedding 预热完成：{}", m)
    return m


def embed(text: str) -> list[float] | None:
    """文本 → 向量（1024 维 bge-m3 / 768 维 bge-small / 768 维哈希）。

    永不抛异常；失败返回 None。带文本缓存（省重复推理）。
    """
    text = _strip(text)
    if not text:
        return None
    cur_mode = mode()
    if text in _emb_cache and _emb_cache[text][0] == cur_mode:
        return _emb_cache[text][1]

    vec = None
    try:
        name = _load_model()
        if _model is not None:
            v = _model.encode([text], normalize_embeddings=True, show_progress_bar=False)
            vec = [float(x) for x in v[0].tolist()]
    except Exception as e:
        logger.warning("[记忆] embedding 推理失败，走哈希回退：{}", str(e)[:100])
        vec = None

    # 缓存条目必须记录「实际生成的向量模式」：推理失败走哈希回退时如果仍按
    # model 模式缓存，会污染缓存——下次命中同一文本返回 768 维哈希向量，
    # 与 1024 维 model collection 混写导致维度不一致、写入被跳过
    cache_mode = cur_mode
    if vec is None:
        vec = _hash_vec(text)
        cache_mode = "hash"

    if len(_emb_cache) >= _EMB_CACHE_MAX:
        _emb_cache.clear()
    _emb_cache[text] = (cache_mode, vec)
    return vec


def _hash_vec(text: str) -> list[float]:
    """零依赖回退：字符二元组 + 哈希投影成定长向量（无语义，仅词面相似兜底）。

    保证系统在无模型 / 无网络时仍有"向量接口"可用（调用方会与 TF-IDF 融合）。
    """
    bigrams = [text[i : i + 2] for i in range(max(0, len(text) - 1))]
    if not bigrams:
        bigrams = [text]
    vec = [0.0] * _HASH_DIM
    for g in bigrams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % _HASH_DIM
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


def embed_batch(texts: list[str]) -> list[list[float]] | None:
    """批量 embedding（比逐条快）。失败返回 None。"""
    clean = [_strip(t) for t in texts]
    if not clean:
        return []
    cur_mode = mode()
    missing = [t for t in clean if t and (t not in _emb_cache or _emb_cache[t][0] != cur_mode)]
    if missing:
        try:
            name = _load_model()
            if _model is not None:
                vs = _model.encode(missing, normalize_embeddings=True, show_progress_bar=False)
                for t, v in zip(missing, vs):
                    vec = [float(x) for x in v.tolist()]
                    if len(_emb_cache) >= _EMB_CACHE_MAX:
                        _emb_cache.clear()
                    _emb_cache[t] = (cur_mode, vec)
        except Exception as e:
            logger.warning("[记忆] 批量 embedding 失败：{}", str(e)[:100])
    out = []
    for t in clean:
        if not t:
            out.append(_hash_vec(""))
            continue
        if t not in _emb_cache or _emb_cache[t][0] != cur_mode:
            # 回退哈希向量同样按 "hash" 模式缓存（与单条 embed 的行为一致）
            _emb_cache[t] = ("hash", _hash_vec(t))
        out.append(_emb_cache[t][1])
    return out


def dim() -> int:
    """当前向量维度（用于 Chroma collection 声明）。"""
    try:
        v = embed("维度探测")
        if v:
            return len(v)
    except Exception:
        pass
    return _HASH_DIM
