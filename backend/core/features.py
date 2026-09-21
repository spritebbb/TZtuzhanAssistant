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
    "compact_ui_enabled": True,
    "aesthetics_enabled": os.getenv("FEATURE_AESTHETICS_ENABLED", "1").lower() not in {"0", "false", "off"},
    "memory_lifecycle_enabled": os.getenv("FEATURE_MEMORY_LIFECYCLE_ENABLED", "1").lower()
        not in {"0", "false", "off"},
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
    # F01：基于真实素材的问候与变体池。关闭后问候退回旧素材逻辑（离线叙事
    # 与兜底池），不破坏原 gap 门控与并发去重；不选变体、不记冷却。
    "greeting_material_enabled": True,
    # G04：她的求助与愿望（亲密+信任双门槛时偶尔请对方帮个小忙）。关闭后
    # 不再生成候选；已发出的请求仍可回应，已接受的产物保留。
    "companion_requests_enabled": True,
    # L04：幽默记忆（梗的授权/冷却/退役）。关闭后不再选梗注入，既有共同
    # 语言注入行为不变；反馈与退役仍可登记（不影响用户主权操作）。
    "humor_memory_enabled": True,
    # F04：专注收尾人格化与可重试投递。关闭后完成专注不再入箱，原计时与
    # 完成流程不变（退回原文案语义）。
    "focus_wrapup_enabled": True,
    # F06：聊天意图自动预填草稿。关闭后不再产草稿，原手动创建入口保留。
    "activity_drafts_enabled": True,
    # P3-05B：本地质量统计（延迟/失败规则/重复率/来源选择/明确反馈计数）。
    # 默认本地记录、可关可清；临时轮不写；不属于关系包。
    "experience_metrics_enabled": os.getenv("FEATURE_EXPERIENCE_METRICS_ENABLED", "1").lower()
        not in {"0", "false", "off"},
    # Q3：本地最小遥测与来源链。关闭后立即停止新记录，已有数据可单独清理。
    "telemetry_enabled": os.getenv("FEATURE_TELEMETRY_ENABLED", "1").lower()
        not in {"0", "false", "off"},
    # L16：可选择共享知识。默认全部隔离；只有用户明确「分享给某角色」才生效。
    "shared_resources_enabled": os.getenv("FEATURE_SHARED_RESOURCES_ENABLED", "1").lower()
        not in {"0", "false", "off"},
    # D1：场景化表达观察（user_style_map 复活）。关闭后停止注入已观察到的
    # 表达习惯；提炼与用户主权操作（查看/删除）不受影响。
    "style_map_enabled": True,
    # 酒馆同玩（SillyTavern「菟菚同伴」扩展点名她说话）。关闭后 /api/tavern/*
    # 一律 403；卡/世界书素材的不可信包裹与裁决规则不依赖本开关。
    "tavern_enabled": True,
    # D12 主动意愿 roll：necessity 达标的主动候选再掷一次「她想不想说」；
    # 硬规则源（约定到点等）与用户直接对话永不进骰子。关闭后回到纯 necessity 门。
    "willingness_enabled": True,
    # D9 局势档案：≤2.5k tokens 恒定世界快照常驻注入（目标/约定/悬念/近事件/
    # 生活/焦点），钩子不靠检索命中。派生态可随时重编译重建；关闭时整层退场。
    "situation_enabled": True,
    # D11 离线补算：重开后把她离线期间照常过的日子逐条说给你（可跳过）；
    # 确定性重放真实行程，超限降级摘要，开场一句才用 LLM。
    "offline_recap_enabled": True,
    # L12 公网网关：独立 gateway 进程 + Cloudflare Tunnel + Access（默认关；
    # 设备注册/推送端点与源站 JWT 校验模块已就位，域名与 Access 配置后开启）。
    "remote_gateway_enabled": False,
    # P3-03 本地语音（GPT-SoVITS）：当前人格有启用声纹且能力协商成功时优先
    # 用本地合成，任何一步不满足显式回退 edge-tts。默认关，装好服务再开。
    "local_tts_enabled": False,
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
