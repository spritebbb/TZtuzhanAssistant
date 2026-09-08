"""功能开关系统：动态控制各功能开启/关闭。

Web UI 面板写入 data/feature_flags.json，bot 在 pipeline 注入前动态检查。
默认全开（文件不存在或未配置的开关视为 True）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .config import config

_FLAGS_PATH = config.data_dir / "feature_flags.json"
_cache: dict = {"data": {}, "ts": 0.0}
_CACHE_TTL = 5.0
# 写锁：set_flag 是读-改-写，两个请求并发会互相覆盖（丢失其中一个开关的变更）
import threading

_write_lock = threading.Lock()

# 所有可用开关及其默认值。
# 注意：只保留「有消费方」的动态开关。贴纸现由 STICKER_ENABLED 等环境
# 配置管理，不在这个仅有内部写端、尚无 UI 的动态开关表中重复维护。
FLAG_DEFAULTS = {
    "memory_salience_enabled": os.getenv("FEATURE_MEMORY_SALIENCE_ENABLED", "1").lower()
        not in {"0", "false", "off"},
    "profile_enabled": True,       # 用户画像（pipeline 注入时检查，唯一活跃开关）
    # P0-01A：用户可见回复在发送/持久化前统一检查。实现与测试已稳定
    # （P3 验收 + test_output_hygiene.py），设置页有开关入口，默认开启；
    # 关闭时 pipeline 保持旧流式契约。
    "output_hygiene_enabled": True,
    # P1-02：语境注册表接管共同清单语境（首期唯一 provider）。实现与测试
    # 已稳定（test_context_registry.py），设置页有开关入口，默认开启；
    # 关闭时走 colists.list_context 旧路径；两条路径互斥，不会双注入。
    "context_registry_enabled": True,
    # L06：低频生活模板池（她低频出门/换活动）。用户拍板（2026-09-08）：
    # 接主动候选源、概率 0.25、用户意见可取消、出门可沉默但事件/状态行
    # 必须可见。关闭后 choose_life_event 与外出主动候选全部停用。
    "life_templates_enabled": True,
    # L03：长期关系气质（陪伙伴/玩闹/知心/成长/浪漫）。证据只来自明确事件，
    # derive_style 不回分数；关闭后 evidence 停止登记、derive 返回 forming。
    "relationship_style_enabled": True,
}


def _load() -> dict:
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]
    try:
        with open(_FLAGS_PATH, encoding="utf-8") as f:
            _cache["data"] = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _cache["data"] = {}
    _cache["ts"] = now
    return _cache["data"]


def flag(name: str) -> bool:
    """读取某个功能开关；不存在则用默认值（True）。"""
    data = _load()
    return data.get(name, FLAG_DEFAULTS.get(name, True))


def set_flag(name: str, value: bool) -> None:
    """写入开关值（同时清缓存）；原子写避免读到半截 JSON。

    写入端：设置页功能开关面板（POST /api/flags）。"""
    if name not in FLAG_DEFAULTS:
        return  # 只接受已知开关名
    with _write_lock:  # 串行化读-改-写，避免并发覆盖
        data = {}
        if _FLAGS_PATH.exists():
            try:
                with open(_FLAGS_PATH, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = {}
        data[name] = bool(value)
        # 原子写：写临时文件再替换，防止并发读读到损坏 JSON
        tmp = _FLAGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_FLAGS_PATH)
        _cache["data"] = data
        _cache["ts"] = time.time()


def all_flags() -> dict[str, bool]:
    """返回所有开关的当前值（含默认值）。"""
    data = _load()
    result = {}
    for k, default in FLAG_DEFAULTS.items():
        result[k] = data.get(k, default)
    return result
