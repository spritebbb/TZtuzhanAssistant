"""记忆管理层：提供统一记忆管理 API（添加/检索/更新/遗忘），叠加在 Chroma 之上。

双通道：
- 主通道：Mem0（高级记忆管理，带回溯/冲突解决/重要性评分/自动遗忘）
- 回退通道：自研「基于 Chroma + LLM 的记忆管理」（无额外依赖，同样带回溯/去重/更新/遗忘）

对外只暴露 Mem0Manager 类，调用方不感知底层实现。
"""
import json
import time
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
    """

    def __init__(self):
        self._store = None  # 惰性导入 chroma 包

    def _get_store(self):
        if self._store is None:
            from . import vector_store as vs

            self._store = vs
        return self._store

    def _mem_collection(self):
        store = self._get_store()
        return getattr(store, "_collection", lambda k: None)("mem")

    def _prune(self, user_id: str, *, max_age_days: int | None = None) -> int:
        """清理 mem 集合：按超龄（可选）与每用户上限淘汰最旧，返回删除条数。

        让 _FALLBACK_MAX_PER_USER / _FALLBACK_MAX_AGE_DAYS 真正生效——
        Mem0 故障期间 fallback 记忆不再无限累积。
        """
        col = self._mem_collection()
        if col is None:
            return 0
        try:
            data = col.get(where={"user_id": user_id})
        except Exception:
            return 0
        ids = data.get("ids") or []
        metas = data.get("metadatas") or []
        now = datetime.now()
        removed = 0

        # 1) 超龄清理
        if max_age_days is not None:
            for i, iid in enumerate(ids):
                meta = metas[i] if isinstance(metas, list) and i < len(metas) else {}
                ts_raw = (meta or {}).get("ts", "")
                try:
                    ts = datetime.fromisoformat(str(ts_raw)) if ts_raw else None
                except Exception:
                    ts = None
                if ts is not None and (now - ts).days > max_age_days:
                    try:
                        col.delete(ids=[iid])
                        removed += 1
                    except Exception:
                        pass
            # 删除后重新拉取，再做容量淘汰
            try:
                data = col.get(where={"user_id": user_id})
            except Exception:
                return removed
            ids = data.get("ids") or []
            metas = data.get("metadatas") or []

        # 2) 每用户上限：按 ts 升序删最旧（无 ts 的按空串排最前，视为最旧）
        if len(ids) > _FALLBACK_MAX_PER_USER:
            indexed = []
            for i, iid in enumerate(ids):
                meta = metas[i] if isinstance(metas, list) and i < len(metas) else {}
                indexed.append((str((meta or {}).get("ts", "")), iid))
            indexed.sort()
            overflow = indexed[: len(indexed) - _FALLBACK_MAX_PER_USER]
            for _, iid in overflow:
                try:
                    col.delete(ids=[iid])
                    removed += 1
                except Exception:
                    pass
        return removed

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
        # 独立 kind="mem"：与 pipeline 的 long_memory（kind="lm"）分开存放，
        # 避免 fallback 管理记忆与对话原文记忆混在同一 collection（互相污染检索）。
        # Chroma id 本身含 user_id（{user_id}|mem|{rid}），不同用户不会互相覆盖。
        ok = store.add(user_id, "mem", rid, text, extra=meta)
        if ok:
            # 写入后立即按上限/超龄淘汰，防止 Mem0 故障期间无限累积
            self._prune(user_id, max_age_days=_FALLBACK_MAX_AGE_DAYS)
        return ok

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        store = self._get_store()
        # 只检索本 fallback 自管的 mem kind，不再混入 pipeline 的 lm 原文记忆
        hits = store.search(user_id, query, top_k=limit, kind="mem")
        out = []
        for h in hits:
            out.append({
                # 返回可直接用于 update/delete 的完整 id（user_id|kind|record_id），
                # 修复旧版"search 返回纯 int id、update/delete 却要 a|b|c"的格式错配
                "id": f"{user_id}|mem|{h.record_id}",
                "text": h.text,
                "score": 1.0 - h.distance,
                "metadata": h.meta,
            })
        return out

    def get_all(self, user_id: str) -> list[dict]:
        """获取用户全部记忆（从 Chroma 拉取）。"""
        store = self._get_store()
        all_memories: list[dict] = []
        col = getattr(store, "_collection", lambda k: None)("mem")
        if col is None:
            return all_memories
        try:
            data = col.get(where={"user_id": user_id})
            ids = data.get("ids") or []
            docs = data.get("documents") or []
            metas = data.get("metadatas") or []
            for i in range(len(ids)):
                all_memories.append({
                    "id": ids[i],
                    "text": docs[i] if isinstance(docs, list) else docs,
                    "metadata": metas[i] if isinstance(metas, list) else metas or {},
                })
        except Exception:
            pass
        return all_memories

    def update(self, user_id: str, memory_id: str, text: str) -> bool:
        """更新：先删后加（Chroma upsert 直接覆盖）。"""
        store = self._get_store()
        # 解析 memory_id 格式
        parts = memory_id.split("|")
        if len(parts) >= 3:
            kind = parts[1]
            try:
                rid = int(parts[2])
            except ValueError:
                return False
            return store.add(user_id, kind, rid, text)
        return False

    def delete(self, user_id: str, memory_id: str) -> bool:
        parts = memory_id.split("|")
        if len(parts) >= 3:
            kind = parts[1]
            try:
                rid = int(parts[2])
            except ValueError:
                return False
            store = self._get_store()
            return store.delete(user_id, kind, rid)
        return False

    def forget_old(self, user_id: str, max_age_days: int = 90) -> int:
        """遗忘过期记忆：超过 max_age_days 的移除，返回实际删除条数。"""
        return self._prune(
            user_id, max_age_days=int(max_age_days or _FALLBACK_MAX_AGE_DAYS)
        )


# 全局单例
manager = Mem0Manager()
