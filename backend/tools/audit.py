# -*- coding: utf-8 -*-
"""工具审计：所有工具调用落盘 data/tool_log.jsonl，含确认状态与结果摘要。

每条记录字段：
- ts: 时间戳（ISO）
- user: 用户身份（current_user_id 或默认）
- tool: 工具名
- args: 参数摘要（敏感参数脱敏）
- confirmed: 确认状态（auto / allow / deny / timeout / blocked）
- ok: 是否成功
- elapsed_ms: 耗时
- result: 结果摘要（前 200 字符）
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from ..core.config import config

_LOG_PATH: Path = config.data_dir / "tool_log.jsonl"

_lock = threading.Lock()

# 敏感参数名（写入日志时脱敏为 ****）
_SENSITIVE_KEYS = ("api_key", "token", "password", "secret", "authorization", "key")

# 结果摘要上限（日志内，不影响注入上下文的完整结果）
_RESULT_SUMMARY = 200


def _mask_args(args: dict) -> dict:
    """参数摘要：敏感键脱敏、超长值截断。"""
    if not isinstance(args, dict):
        return {"_": str(args)[:100]}
    out: dict = {}
    for k, v in args.items():
        if any(s in str(k).lower() for s in _SENSITIVE_KEYS):
            out[str(k)] = "****"
        elif isinstance(v, str) and len(v) > 120:
            out[str(k)] = v[:120] + f"...({len(v)}字符)"
        else:
            out[str(k)] = v
    return out


def _result_summary(output: str) -> str:
    s = output or ""
    s = s.replace("\n", " ").strip()
    return s[: _RESULT_SUMMARY] + ("..." if len(s) > _RESULT_SUMMARY else "")


def log_tool_call(
    *,
    tool: str,
    args: dict | None = None,
    confirmed: str = "auto",
    ok: bool = True,
    elapsed_ms: int = 0,
    result: str = "",
    error: str = "",
    user: str = "",
) -> None:
    """写一条工具调用审计日志（失败静默，不影响工具流程）。"""
    try:
        record = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "user": user or "assistant-main",
            "tool": tool,
            "args": _mask_args(args or {}),
            "confirmed": confirmed,
            "ok": bool(ok),
            "elapsed_ms": elapsed_ms,
            "result": _result_summary(result),
            "error": (error or "")[:200],
        }
        with _lock:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 审计失败绝不干扰主流程


def recent_log(n: int = 50) -> list[dict]:
    """读取最近 n 条审计记录（供前端/调试查看）。"""
    return query_log(limit=n, offset=0)


def _log_files() -> list[Path]:
    """返回审计日志文件列表，按时间升序（旧 → 新）。

    含轮转文件 tool_log.N.jsonl（N 越大越旧）与主文件 tool_log.jsonl（最新）。
    轮转后查询侧若只读主文件会漏掉历史记录，这里统一纳入，保证审计追溯不因
    轮转断裂。排序：.keep → ... → .1 → 主文件。
    """
    try:
        rotated = sorted(
            _LOG_PATH.parent.glob("tool_log.*.jsonl"),
            key=lambda p: int(p.stem.rsplit(".", 1)[-1]),
            reverse=True,  # 数字大者（更旧）在前
        )
        files = rotated
        if _LOG_PATH.exists():
            files = files + [_LOG_PATH]
        return files
    except Exception:
        return [_LOG_PATH] if _LOG_PATH.exists() else []


def _load_all() -> list[dict]:
    """读取全部审计记录（含轮转文件，已解析），无记录返回空列表。"""
    try:
        records = []
        with _lock:
            for path in _log_files():
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    continue
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        continue
        return records
    except Exception:
        return []


# 尾读窗口：无过滤条件时最多读文件尾部的行数（配合轮转，查询成本恒定）
_TAIL_LINES = 4000


def _load_tail(max_lines: int = _TAIL_LINES) -> list[dict]:
    """从文件尾按块倒读 max_lines 行（无过滤查询用，避免全量读入大文件）。"""
    try:
        if not _LOG_PATH.exists():
            return []
        with _lock:
            with open(_LOG_PATH, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                chunk = 65536
                pos = size
                tail = b""
                while pos > 0 and tail.count(b"\n") <= max_lines:
                    step = min(chunk, pos)
                    pos -= step
                    f.seek(pos)
                    tail = f.read(step) + tail
        lines = tail.decode("utf-8", errors="replace").splitlines()
        if pos > 0 and lines:
            lines = lines[1:]  # 首行可能被块边界截断，丢弃
        records = []
        for line in lines[-max_lines:]:
            try:
                r = json.loads(line)
                if isinstance(r, dict):
                    records.append(r)
            except Exception:
                continue
        return records
    except Exception:
        return []


def _matches(rec: dict, *, tool: str | None, confirmed: str | None,
             ok: bool | None, q: str | None) -> bool:
    if tool:
        tools = {t.strip().lower() for t in tool.split(",") if t.strip()}
        if rec.get("tool", "").lower() not in tools:
            return False
    if confirmed and rec.get("confirmed", "") != confirmed:
        return False
    if ok is not None and bool(rec.get("ok")) != ok:
        return False
    if q:
        ql = q.lower()
        hay = f"{rec.get('tool','')} {rec.get('result','')} {rec.get('error','')}".lower()
        if ql not in hay:
            return False
    return True


def query_log(*, limit: int = 100, offset: int = 0,
              tool: str | None = None, confirmed: str | None = None,
              ok: bool | None = None, q: str | None = None) -> list[dict]:
    """按条件查询审计记录（倒序：最新在前）。

    无过滤条件时走尾部读取（成本与文件大小无关）；带过滤条件仍需全量扫描。
    """
    use_tail = not (tool or confirmed or ok is not None or q)
    all_records = _load_tail() if use_tail else _load_all()
    filtered = [r for r in all_records if _matches(
        r, tool=tool, confirmed=confirmed, ok=ok, q=q)]
    # 毫秒级 ts 可能重复：以文件序（后写更新）做平局裁决，保证严格倒序
    ordered = sorted(
        enumerate(filtered),
        key=lambda pair: (pair[1].get("ts", ""), pair[0]),
        reverse=True,
    )
    sliced = [r for _, r in ordered[offset:offset + limit]]
    return sliced


def count_log(*, tool: str | None = None, confirmed: str | None = None,
              ok: bool | None = None, q: str | None = None) -> int:
    """符合条件的记录总数。"""
    use_tail = not (tool or confirmed or ok is not None or q)
    all_records = _load_tail() if use_tail else _load_all()
    return sum(1 for r in all_records if _matches(
        r, tool=tool, confirmed=confirmed, ok=ok, q=q))


def clear_log() -> int:
    """清空审计日志，返回清除条数。

    清除动作本身记一条「清除审计」记录（不可清），保留审计痕迹——避免审计日志
    被静默清空后无从追溯。同时清理轮转文件，保证「清空」语义完整。
    """
    try:
        with _lock:
            n = 0
            if _LOG_PATH.exists():
                n = sum(1 for _ in _LOG_PATH.open(encoding="utf-8"))
            # 主文件 + 轮转文件一并清除
            for path in _log_files():
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    continue
            # 清除后立刻写入一条审计记录，标记本次清除动作
            record = {
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "user": "system",
                "tool": "__audit__",
                "args": {"action": "clear_log", "cleared": n},
                "confirmed": "allow",
                "ok": True,
                "elapsed_ms": 0,
                "result": f"已清空审计日志（{n} 条）",
                "error": "",
            }
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return n
    except Exception:
        return 0
