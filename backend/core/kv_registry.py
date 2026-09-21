# -*- coding: utf-8 -*-
"""kv_store 键登记表（短名单债务 #5）。

kv_store 是 schema-less 杂物抽屉；这份登记表给每个键（模式）登记归属模块、
用途、生命周期与导出策略，供关系导出器按清单枚举，也为将来做选择性导出、
加密与清理提供唯一依据。

纪律：新增 kv 键时必须在这里登记（`tests/test_relationship_bundle.py` 里有
覆盖性检查，运行若干真实流程后 kv 中出现未登记的键会失败）。

lifecycle:
- daily      按天滚动，无需导出（隔天自动失效）
- runtime    运行时状态/去重锚点，可由数据重新推导，不导出
- transient  生成中的占位，进程内短暂存在，不导出
export:
- False      当前所有键都是运行态；未来出现「用户设置」类持久键时置 True
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KvKeySpec:
    pattern: str          # 字面量或含 {day}/{user_id}/{key} 占位的模式
    module: str
    purpose: str
    lifecycle: str        # daily / runtime / transient
    export: bool = False
    sensitive: bool = False


KV_KEY_SPECS: tuple[KvKeySpec, ...] = (
    KvKeySpec("proactive:done:{day}", "proactive_policy", "每日主动额度计数（跨通道共享）", "daily"),
    KvKeySpec("proactive:claim:{day}", "proactive_policy", "主动生成原子占位", "transient"),
    KvKeySpec("proactive:failure:{day}", "proactive_policy", "主动失败冷却标记", "daily"),
    KvKeySpec("initiative:{day}:{user_id}", "proactive_policy", "旧版每日主动去重键（兼容保留）", "daily"),
    KvKeySpec("initiative:pending", "initiative", "待投递主动消息队列", "transient"),
    KvKeySpec("initiative:archive_suggest:{day}", "initiative", "归档建议每日去重", "daily"),
    KvKeySpec("initiative:planner_expressed:{day}", "initiative", "心事表达每日一条去重", "daily"),
    KvKeySpec("initiative:promise_followup:{day}", "initiative", "约定跟进每日一次去重", "daily"),
    KvKeySpec("surprise:last", "surprise", "上次惊喜日期（低频间隔门控）", "runtime"),
    KvKeySpec("surprise:last_artifact", "surprise", "上次惊喜使用的产物 id（素材轮换）", "runtime"),
    KvKeySpec("web_last_seen", "greeting", "久别问候的上次访问时间", "runtime"),
    KvKeySpec("web_greet_pending", "greeting", "问候生成中占位", "transient"),
    KvKeySpec("rhythm:followups", "conversation_rhythm", "有期限追发实例（只存来源摘要）", "runtime"),
    KvKeySpec("daily_batch:{day}", "daily", "每日事实提炼批次完成标记", "daily"),
    KvKeySpec("bonus:{day}:{key}", "affection", "好感度彩蛋（夸奖/道歉等）每日去重与聊天计数", "daily"),
    KvKeySpec("reported_stage", "pipeline", "已向用户播报的关系阶段锚点", "runtime"),
    KvKeySpec("c4:last_stage_rank", "unlock", "解锁检测的上次阶段锚点", "runtime"),
    KvKeySpec("c4:last_bond_rank", "unlock", "解锁检测的上次羁绊锚点", "runtime"),
    KvKeySpec("c4:last_substage_rank", "unlock", "解锁检测的上次小档切点锚点（P2-01）", "runtime"),
    KvKeySpec("sticker:last_message_id", "stickers", "贴纸频率控制的消息间隔锚点", "runtime"),
    KvKeySpec("state:rest", "state", "精力/休息状态机（是否在休息）", "runtime", export=True),
    KvKeySpec("sleep:wake_until", "sleep_gate", "连续消息唤醒后的临时可聊窗口", "runtime"),
    KvKeySpec("state:emotions", "emotion_state", "离散情绪有界快照（P1-03，≤3 条活跃情绪）", "runtime", export=True),
    KvKeySpec("state:emotion_repairs", "emotion_state", "每来源一次性修复记录（道歉/安抚）", "runtime"),
    KvKeySpec("state:schedule", "schedule", "行程状态机（当前块/能量记账/推进锚点，P1-04）", "runtime"),
    KvKeySpec("state:life_templates", "life_templates", "L06 生活模板冷却/取消状态（last_used/vetoed_date）", "runtime"),
    KvKeySpec("life_templates:outing_expressed:{day}", "life_templates", "L06 外出归来主动汇报每日去重", "daily"),
    KvKeySpec("state:calendar", "calendar_modulation", "日历阶段幂等状态（P1-05，近 8 天已见 phase）", "runtime"),
    KvKeySpec("state:relationship_tension", "state", "关系张力 0-100（修复期语气依据）", "runtime", export=True),
    KvKeySpec("state:emotion_memory", "state", "情绪记忆（跨天连续的心情素材）", "runtime", export=True),
    KvKeySpec("state:emotion_archive", "state", "情绪记忆归档（可回看的历史情绪）", "runtime", export=True),
    KvKeySpec("state:event_memory", "state", "事件记忆（她记着的最近互动信号）", "runtime", export=True),
    KvKeySpec("attention:topics", "attention_state", "§17.1 注意力主题（≤5 个 topic id + 权重，无正文）", "runtime"),
    KvKeySpec("proactive:source_last:{source}", "expression_policy", "§17.1 各主动来源最近一次投递时间（重复惩罚输入）", "runtime"),
    KvKeySpec("proactive:willingness", "expression_policy", "D12 意愿 roll 连续未中计数（想念补偿输入）", "runtime"),
    KvKeySpec("cost:hint_day", "cost_guard", "D10 叙事提示每日一次标记", "daily"),
    KvKeySpec("cost:skipped_batch:{day}", "cost_guard", "D10 daily 批处理被熔断跳过的 journal 记录", "runtime"),
    KvKeySpec("offline:pending", "offline_recap", "D11 待回放的离线补算（事件+开场+state）", "runtime"),
    KvKeySpec("offline:last_recap", "offline_recap", "D11 上一次已确认/跳过的补算归档", "runtime"),
)


def match_spec(key: str) -> KvKeySpec | None:
    """把实际键匹配到登记项：先精确匹配，再按 {占位} 模板前缀匹配。"""
    if key in {spec.pattern for spec in KV_KEY_SPECS}:
        return next(spec for spec in KV_KEY_SPECS if spec.pattern == key)
    for spec in KV_KEY_SPECS:
        if "{" not in spec.pattern:
            continue
        prefix = spec.pattern.split("{", 1)[0]
        if prefix and key.startswith(prefix):
            return spec
    return None
