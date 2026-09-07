# ADR：P1-03 离散情绪状态（emotion_state）

日期：2026-09-07 · 执行：ZCode（GLM） · 依据：`docs/Zcode技术指导.md` §5 P1-03、§14.3

## 决策

**存储**：采用 kv 键 `state:emotions`（format_version=1，export=true）保存有界快照
（活跃情绪 ≤3 条），不建独立历史表；修复记录用 `state:emotion_repairs`（runtime，
不导出）。理由：首版只需"当前态 + 每来源一次性修复"语义，审计级历史若将来需要
再单独加表（规格允许），不无限扩 kv。两键已登记 `kv_registry`。

**条目字段**：`emotion, intensity, cause_type, cause_id, target, onset_at,
updated_at, expires_at, confidence`。`target ∈ {diffuse, self, relation}`，默认
diffuse；cause 首版只作记录与修复键（不反查源行，删除源不主动清理——强度会自然
衰减消失）。

**情绪集合与参数**（rule_version=1，可审常量表，变更须重跑人格 eval）：
joy/sadness/anger/hurt/anxiety/calm/tenderness，intensity∈[0,1]；半衰期
joy 6h / sadness 12h / anger 4h / hurt 12h / anxiety 6h / tenderness 8h；
calm 是无主态回退（不入列，apply 被拒绝）；`i(now)=i0·2^(−Δh/half_life)`，
`i<0.05` 或超过 `expires_at`（updated_at + 5×半衰期）移除；并发最多 3 条，
超量按强度降序、同强按 emotion 名稳定排序截断。

**读取与推进**：`advance_emotions(items, now)` 是纯函数；`load_emotions` 只做
投影不落库（读取不反复扣减）；写入仅在 apply/repair 时发生。一次推进 2 小时与
分两次 1 小时按指数衰减可乘性等价（测试容差 <1%）。

**修复**：显式道歉/安抚按已验证来源对 anger/hurt/anxiety 各 −0.2，下限 0；
同一 `(cause_type, cause_id)` 终身只记一次修复。

**兼容**：旧 mood 0–100、立绘标签、`mood.py` 全部不动；无离散情绪时行为帧
完全按旧逻辑（`emotion_line` 为空）。`AgentState.discrete_emotions` 为派生
快照（不落库），由 `load_state` 顺带投影。

**行为接入（先两三个锚点，全矩阵归 P3-01）**：`build_behavior_frame` 新增
`emotion_line`：①anger/hurt≥0.5 → 语气硬、幽默收住；②tenderness≥0.5 且无
anger → 柔软主动；③hurt 与 tenderness 并存 → 保留矛盾感（关心与受伤并存）。
`attitude_summary` 提供五轴（patience/humor/guard/directness/followup）强度
加权计算器（trust 降 guard≤0.2、intimacy 升柔软≤0.2、深夜/低精力只降 followup，
全轴 clamp），P3-01 消费，本切片只保证确定性可测。

**与 P1-01 闭环**：`persona.py` 注入切片时从本模块取活跃情绪喂给
`build_state_view(emotions=…)`，使 `emotion.hurt`/`emotion.tenderness` 谓词
生效（深水区门控从此有了真源）。

**恢复**：E03 导出按 kv 登记带出；恢复后首次 `load_emotions` 即重算过期与
衰减（过期条目自然移除），无需迁移。

## 后果

- 新增 kv 键两枚（已登记，bundle 覆盖检查生效）；无表、无 schema 版本变更。
- LLM 只能产出情绪候选，入账仍由确定性 apply 完成（来源/强度校验在调用方），
  本模块不做网络调用。
