"""记忆管理层：提供统一记忆管理 API（添加/检索/更新/遗忘），叠加在 Chroma 之上。

双通道：
- 主通道：Mem0（高级记忆管理，带回溯/冲突解决/重要性评分/自动遗忘）
- 回退通道：自研「基于 Chroma + LLM 的记忆管理」（无额外依赖，同样带回溯/去重/更新/遗忘）

对外只暴露 Mem0Manager 类，调用方不感知底层实现。
"""
import json
import logging
import time
import warnings
from datetime import datetime, timedelta
from typing import Any

from ..config import config
from ..log import logger

# 自研回退管理的超参数
_FALLBACK_MAX_AGE_DAYS = 90  # 超过此天数的记忆自动遗忘
_FALLBACK_MAX_PER_USER = 200  # 每用户记忆上限
_FALLBACK_IMPORTANCE_CUTOFF = 0.3  # 重要性低于此阈值的记忆优先遗忘
# Mem0 降级策略：连续失败达到阈值才降级；降级后经过冷却期允许自动重建
_DEGRADE_THRESHOLD = 3
_DEGRADE_COOLDOWN_SEC = 300.0
# 清除时统计条数用的 top_k：get_all 默认只回 20 条，会低估「已清除 N 条」
_COUNT_TOP_K = 5000

# Mem0 及其依赖（sentence-transformers / chroma / huggingface_hub 等）在初始化时
# 会通过 Python warnings 和 stdlib logging 打印一批无害噪音（方法改名 FutureWarning、
# chroma 不支持关键字搜索、spaCy 未安装、遥测提示等）。这些与运行正确性无关，
# 但会污染启动日志。在真正 import mem0 前统一静音，避免影响项目自身的 loguru 日志。
def _quiet_mem0_noise() -> None:
    # 1) Python warnings：仅忽略已知无害噪音，不做全局关闭。
    #    warnings.filterwarnings 的 message 用的是 re.match（从头匹配），故须以 .* 开头
    _known_noise = (
        # sentence-transformers：get_sentence_embedding_dimension 改名
        r".*get_sentence_embedding_dimension.*get_embedding_dimension",
        # chroma：不支持关键字搜索（仅降级为语义检索，功能仍可用）
        r".*chroma.*does not support keyword search",
        # 可选优化组件缺失提示（spaCy），无碍基础功能
        r".*spaCy is not installed",
    )
    for msg in _known_noise:
        warnings.filterwarnings("ignore", message=msg)

    # 2) stdlib logging：这些库的 WARNING/INFO 无价值，提到 ERROR 级别即不再落到 stderr
    for name in (
        "sentence_transformers",
        "transformers",
        "huggingface_hub",
        "tokenizers",
        "chromadb",
        "mem0",
        "httpx",
        "httpcore",
        "openai",
        "urllib3",
        "PIL",
    ):
        try:
            logging.getLogger(name).setLevel(logging.ERROR)
        except Exception:
            pass


class Mem0Manager:
    """记忆管理器，优先用 Mem0，失败回退到自研实现。"""

    def __init__(self):
        self._mem0 = None
        self._fallback = None
        # 降级状态（供 stats() 暴露给上层展示）
        self._degraded = False
        self._degraded_ts = 0.0
        self._last_error = ""
        self._fail_count = 0
        self._last_fail_ts = 0.0

    def _ensure_ready(self):
        """惰性初始化（含降级后的冷却期自动重建）。"""
        if self._mem0 is not None:
            return True
        if self._fallback is not None:
            # 已降级到 fallback：冷却期过后尝试重建 Mem0 通道，避免"一次瞬时
            # 错误永久关停 Mem0、只能靠重启恢复"
            if not self._degraded:
                return True
            if time.monotonic() - self._degraded_ts < _DEGRADE_COOLDOWN_SEC:
                return True
            self._fallback = None
            self._degraded = False
        return self._init_mem0()

    def _init_mem0(self) -> bool:
        """尝试初始化 Mem0；失败时建立 fallback 并记录降级状态。"""
        # 尝试 Mem0 初始化
        try:
            import os

            # 禁用 Mem0 的 PostHog 遥测（避免每次调用打印噪音日志）
            os.environ.setdefault("POSTHOG_DISABLED", "1")
            os.environ.setdefault("MEM0_TELEMETRY", "False")
            # 静音 Mem0 依赖链的已知无害噪音（警告/第三方日志）
            _quiet_mem0_noise()
            from mem0 import Memory

            mem0_config = {
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": config.llm_model,
                        "api_key": config.llm_api_key,
                        "openai_base_url": config.llm_base_url,
                        "temperature": 0.2,
                    },
                },
                "embedder": {
                    "provider": "huggingface",
                    "config": {
                        "model": config.memory_embed_model or "BAAI/bge-m3",
                    },
                },
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "collection_name": "mem0_memories",
                        "path": str(config.data_dir / "chroma_mem0"),
                    },
                },
                "version": "v2.0",
            }
            m = Memory.from_config(mem0_config)
            # 验证可用性（v2.0 用 filters 而非顶层 user_id）
            m.search("test", filters={"user_id": "__probe__"}, limit=1)
            self._mem0 = m
            self._degraded = False
            self._degraded_ts = 0.0
            self._last_error = ""
            self._fail_count = 0
            logger.info("[记忆管理器] Mem0 初始化成功")
            return True
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {str(e)[:120]}"
            self._degraded = True
            self._degraded_ts = time.monotonic()
            logger.warning(
                "[记忆管理器] Mem0 初始化失败（{}），回退到自研管理；{}s 后自动重试",
                self._last_error, _DEGRADE_COOLDOWN_SEC,
            )
            self._fallback = _FallbackManager()
            return True

    def _record_failure(self, op: str, exc: Exception) -> None:
        """记录一次 Mem0 调用失败；连续失败达阈值才降级（瞬时错误不永久关停）。"""
        self._last_error = f"{op}: {type(exc).__name__}: {str(exc)[:120]}"
        now = time.monotonic()
        # 只累计 60s 内的连续失败；隔了很久才失败一次说明通道基本健康
        if now - self._last_fail_ts > 60:
            self._fail_count = 0
        self._fail_count += 1
        self._last_fail_ts = now
        if self._fail_count >= _DEGRADE_THRESHOLD:
            logger.warning(
                "[记忆管理器] Mem0 {} 连续 {} 次失败（{}），降级为 fallback；"
                "{}s 冷却期后自动尝试重建",
                op, _DEGRADE_THRESHOLD, self._last_error, _DEGRADE_COOLDOWN_SEC,
            )
            self._mem0 = None
            self._fallback = _FallbackManager()
            self._degraded = True
            self._degraded_ts = now
        else:
            logger.warning(
                "[记忆管理器] Mem0 {} 失败（{}），暂不降级（连续 {} 次后才降级，"
                "本次保持 Mem0 通道）",
                op, self._last_error, _DEGRADE_THRESHOLD,
            )

    def add(self, user_id: str, text: str, metadata: dict | None = None) -> bool:
        """添加一条记忆。"""
        self._ensure_ready()
        if self._mem0 is not None:
            try:
                self._mem0.add(text, user_id=user_id, metadata=metadata or {})
                return True
            except Exception as e:
                self._record_failure("添加", e)
                if self._mem0 is not None:
                    # 未达降级阈值：保持 Mem0 通道，本次写入失败（下轮会重试）
                    return False
        if self._fallback is not None:
            return self._fallback.add(user_id, text, metadata)
        return False

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        """检索相关记忆，返回 [{"id": ..., "text": ..., "score": ..., "metadata": ...}]。"""
        self._ensure_ready()
        if self._mem0 is not None:
            try:
                results = self._mem0.search(query, filters={"user_id": user_id}, top_k=limit)
                out = []
                for r in (results.get("results") or results):
                    if isinstance(r, dict):
                        out.append({
                            "id": r.get("id", ""),
                            "text": r.get("memory", r.get("text", "")),
                            "score": r.get("score", r.get("relevance", 0.0)),
                            "metadata": r.get("metadata", {}),
                        })
                return out
            except Exception as e:
                self._record_failure("检索", e)
                if self._mem0 is not None:
                    return []
        if self._fallback is not None:
            return self._fallback.search(user_id, query, limit)
        return []

    def get_all(self, user_id: str) -> list[dict]:
        """获取用户全部记忆。"""
        self._ensure_ready()
        if self._mem0 is not None:
            try:
                results = self._mem0.get_all(filters={"user_id": user_id})
                out = []
                for r in (results.get("results") or results):
                    if isinstance(r, dict):
                        out.append({
                            "id": r.get("id", ""),
                            "text": r.get("memory", r.get("text", "")),
                            "metadata": r.get("metadata", {}),
                        })
                return out
            except Exception as e:
                self._record_failure("获取", e)
                if self._mem0 is not None:
                    return []
        if self._fallback is not None:
            return self._fallback.get_all(user_id)
        return []

    def update(self, user_id: str, memory_id: str, text: str) -> bool:
        """更新一条记忆（新信息覆盖旧信息）。"""
        self._ensure_ready()
        if self._mem0 is not None:
            try:
                self._mem0.update(memory_id, data={"memory": text})
                return True
            except Exception as e:
                self._record_failure("更新", e)
                if self._mem0 is not None:
                    return False
        if self._fallback is not None:
            return self._fallback.update(user_id, memory_id, text)
        return False

    def delete(self, user_id: str, memory_id: str) -> bool:
        """删除一条记忆。"""
        self._ensure_ready()
        if self._mem0 is not None:
            try:
                self._mem0.delete(memory_id)
                return True
            except Exception as e:
                self._record_failure("删除", e)
                if self._mem0 is not None:
                    return False
        if self._fallback is not None:
            return self._fallback.delete(user_id, memory_id)
        return False

    def forget_old(self, user_id: str, max_age_days: int = _FALLBACK_MAX_AGE_DAYS) -> int:
        """遗忘过期记忆，返回遗忘条数。"""
        self._ensure_ready()
        if self._mem0 is not None:
            # Mem0 自动处理遗忘，此处返回 0
            return 0
        if self._fallback is not None:
            return self._fallback.forget_old(user_id, max_age_days)
        return 0

    def clear_user(self, user_id: str) -> int:
        """彻底清除该用户的全部管理记忆，返回删除条数（供「失忆重开」调用）。

        Mem0 的向量库是独立目录（data/chroma_mem0），不归 vector_store.clear_user
        管；漏掉它会让重置后的召回继续命中旧记忆（long_term 会拼入上下文）。
        """
        self._ensure_ready()
        if self._mem0 is not None:
            before = self._count_mem0(user_id)
            try:
                self._mem0.delete_all(user_id=user_id)
            except Exception as e:
                self._record_failure("清除", e)
                raise
            return max(0, before - self._count_mem0(user_id))
        if self._fallback is not None:
            return self._fallback.clear_user(user_id)
        return 0

    def _count_mem0(self, user_id: str) -> int:
        """统计该用户在 Mem0 里的记忆条数（用于清除前后报数）。

        get_all 单次有 top_k 上限（默认 20，会低估），这里放大到 _COUNT_TOP_K；
        仍触顶时记一条警告——报数可能低估，但删除本身不受影响。
        """
        try:
            result = self._mem0.get_all(filters={"user_id": user_id}, top_k=_COUNT_TOP_K)
        except Exception:
            return 0
        items = result.get("results") if isinstance(result, dict) else result
        count = len(items or [])
        if count >= _COUNT_TOP_K:
            logger.warning(
                "[记忆管理器] {} 的记忆条数达到统计上限 {}，清除报数可能低估",
                user_id, _COUNT_TOP_K,
            )
        return count

    def stats(self, user_id: str | None = None) -> dict:
        """管理统计信息。"""
        self._ensure_ready()
        base = {
            "degraded": self._degraded,
            "last_error": self._last_error or None,
        }
        if self._degraded:
            base["retry_in_sec"] = max(
                0, int(_DEGRADE_COOLDOWN_SEC - (time.monotonic() - self._degraded_ts))
            )
        if self._mem0 is not None:
            return {"provider": "mem0", "available": True, **base}
        if self._fallback is not None:
            out = {"provider": "fallback", "available": True, **base}
            if user_id:
                out["count"] = len(self._fallback.get_all(user_id))
            return out
        return {"provider": "none", "available": False, **base}


class _FallbackManager:
    """自研记忆管理器（Mem0 不可用时的回退方案）。

    基于 Chroma + LLM 实现记忆管理。核心功能：
    - 去重 & 冲突解决：写入时检查是否与已有记忆冲突，若冲突则用 LLM 合并
    - 重要性评分：每条记忆附带重要性分数（0~1）
    - 自动遗忘：超过上限时移除最不重要的记忆
    - 过期清理：超过最大天数/用户上限时清理

    P0-1 权威分层：manager_memories 表（bot.db）是唯一权威副本，Chroma 的
    mem 分区只是检索索引。此前记忆只写向量库，任何 rebuild（embedding 维度
    变化/加密迁移/关系包恢复）都会整库删除且 migrate 不重灌 mem 源 → 永久
    丢失。现在 add 先落 SQLite、向量尽力而为（失败由 migration 按表计数补灌），
    检索结果按权威表过滤孤儿向量。
    """

    def __init__(self):
        self._store = None  # 惰性导入 chroma 包

    def _get_store(self):
        if self._store is None:
            from . import vector_store as vs

            self._store = vs
        return self._store

    def _db(self):
        from .. import userdb

        return userdb.db

    def _prune(self, user_id: str, *, max_age_days: int | None = None) -> int:
        """清理管理记忆：按超龄（可选）与每用户上限淘汰最旧，返回删除条数。

        以权威表为淘汰依据，向量随行删除；避免 Mem0 故障期间无限累积。
        """
        db = self._db()
        try:
            rows = db.list_manager_memory_rows(user_id)
        except Exception:
            return 0
        if not rows:
            return 0
        now = datetime.now()
        victims: list[int] = []
        survivors: list[int] = []
        for r in rows:
            rid = int(r["rid"])
            ts_raw = r["created_at"] or ""
            try:
                ts = datetime.fromisoformat(str(ts_raw)) if ts_raw else None
            except Exception:
                ts = None
            if (
                max_age_days is not None
                and ts is not None
                and (now - ts).days > max_age_days
            ):
                victims.append(rid)
            else:
                survivors.append(rid)
        # 容量淘汰：survivors 已按 created_at 升序，超出上限删最旧
        if len(survivors) > _FALLBACK_MAX_PER_USER:
            victims.extend(survivors[: len(survivors) - _FALLBACK_MAX_PER_USER])
        if not victims:
            return 0
        try:
            db.delete_manager_memory(user_id, victims)
        except Exception:
            return 0
        # 向量随行删除（尽力而为；失败留下的孤儿会被检索闸门过滤、
        # 并在下次 rebuild 时随整库重灌消失）
        try:
            self._get_store().delete_many(user_id, "mem", victims)
        except Exception:
            pass
        return len(victims)

    def add(self, user_id: str, text: str, metadata: dict | None = None) -> bool:
        store = self._get_store()
        # 用 hash 作为 record_id（去重依据）
        import hashlib

        key = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
        rid = int(key, 16) % (2**31)
        meta = {
            "ts": datetime.now().isoformat(),
            "importance": 0.5,
            **(metadata or {}),
        }
        # 先落权威表：表失败即整体失败（没有权威副本的记忆宁可不收，
        # 也不能只进向量库等一次 rebuild 蒸发）。
        if not self._db().save_manager_memory(
            user_id, rid, text, float(meta.get("importance") or 0.5), meta.get("ts")
        ):
            return False
        # 独立 kind="mem"：与 pipeline 的 long_memory（kind="lm"）分开存放，
        # 避免 fallback 管理记忆与对话原文记忆混在同一 collection（互相污染检索）。
        # Chroma id 本身含 user_id（{user_id}|mem|{rid}），不同用户不会互相覆盖。
        # 向量写入尽力而为：embedding 未就绪/写入失败时由 migration 按表计数补灌。
        store.add(user_id, "mem", rid, text, extra=meta)
        # 写入后立即按上限/超龄淘汰，防止 Mem0 故障期间无限累积
        self._prune(user_id, max_age_days=_FALLBACK_MAX_AGE_DAYS)
        return True

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        store = self._get_store()
        # 只检索本 fallback 自管的 mem kind，不再混入 pipeline 的 lm 原文记忆
        hits = store.search(user_id, query, top_k=limit, kind="mem")
        if not hits:
            return []
        # 权威闸门：过滤权威表已删除的向量孤儿（与 facts 的
        # recallable_fact_ids 同一模式），杜绝「删掉的记忆还在召回」。
        try:
            existing = self._db().manager_memory_existing_rids(
                user_id, [h.record_id for h in hits]
            )
        except Exception:
            existing = {h.record_id for h in hits}
        out = []
        for h in hits:
            if h.record_id not in existing:
                continue
            out.append({
                # 返回可直接用于 update/delete 的完整 id（user_id|kind|record_id）
                "id": f"{user_id}|mem|{h.record_id}",
                "text": h.text,
                "score": 1.0 - h.distance,
                "metadata": h.meta,
            })
        return out

    def get_all(self, user_id: str) -> list[dict]:
        """获取用户全部记忆（以权威表为准）。"""
        try:
            rows = self._db().list_manager_memory_rows(user_id)
        except Exception:
            return []
        return [
            {
                "id": f"{user_id}|mem|{r['rid']}",
                "text": r["content"],
                "metadata": {
                    "ts": r["created_at"],
                    "importance": r["importance"],
                },
            }
            for r in rows
        ]

    def update(self, user_id: str, memory_id: str, text: str) -> bool:
        """更新：删旧（权威表+向量）再按新文本写入（rid 随新内容哈希变化）。

        只接受本管理器自管 mem 分区的 id——拿着 lm/facts 等分区的 id 来
        update 会改写不属于本管理器的向量，与 SQLite 源表脱钩。
        """
        parts = memory_id.split("|")
        if len(parts) < 3 or parts[1] != "mem":
            return False
        try:
            rid = int(parts[2])
        except ValueError:
            return False
        self._db().delete_manager_memory(user_id, [rid])
        self._get_store().delete(user_id, "mem", rid)
        return self.add(user_id, text)

    def delete(self, user_id: str, memory_id: str) -> bool:
        parts = memory_id.split("|")
        if len(parts) >= 3 and parts[1] == "mem":
            try:
                rid = int(parts[2])
            except ValueError:
                return False
            removed = self._db().delete_manager_memory(user_id, [rid])
            self._get_store().delete(user_id, "mem", rid)
            return removed > 0
        return False

    def forget_old(self, user_id: str, max_age_days: int = 90) -> int:
        """遗忘过期记忆：超过 max_age_days 的移除，返回实际删除条数。"""
        return self._prune(
            user_id, max_age_days=int(max_age_days or _FALLBACK_MAX_AGE_DAYS)
        )

    def clear_user(self, user_id: str) -> int:
        """清空该用户的全部管理记忆（权威表 + 向量），返回删除条数。"""
        removed = self._db().clear_manager_memories(user_id)
        try:
            self._get_store().clear_user_kind(user_id, "mem")
        except Exception:
            pass
        return removed


# 全局单例
manager = Mem0Manager()
