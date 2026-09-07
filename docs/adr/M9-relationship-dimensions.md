# ADR：P2-01 信任 × 亲密二维关系迁移

日期：2026-09-07 · 执行：ZCode（GLM） · 依据：`docs/Zcode技术指导.md` §6 P2-01、§14.10/14.10.1

## 决策

**权威与兼容**：`users` 新增 `trust`/`intimacy`（0–100，可空）；旧 `affection`
列降级为兼容派生——每次两维写入时同步为 `min(trust, intimacy)`，全部旧读点
（display/state/unlock/立绘/pipeline 的 `u["affection"]`）自动得到双门槛语义，
不必逐一改造。旧 `stage_of(x)`/`bond_level(x)` 单值函数保留（内部等价于两维
同值），新消费点用 `dimensions_stage(t, i)`/`bond_level_dimensions(t, i)`。

**旧写路径降级**：`db.update_affection`（每日首聊/陪伴/昵称/彩蛋等频次奖励与
旧即时惩罚）不再写 users 任何列，只记 `affection_log`（标 legacy 的互动统计）
——普通聊天频次不刷两维（14.10 拍板）。两维变化只有两个入口：
`apply_relationship_event`（事件入账）与 `set_affection_absolute`（调试入口，
同事务设两维相同并写 manual ledger）。

**迁移**：按 `trust IS NULL` 判首次（不为字段直接 DEFAULT），首次
`trust = intimacy = affection`；重启/二次迁移不覆盖已分化值。旧包恢复缺两维
时按同规则初始化一次。

**事件账本**：新表 `relationship_dimension_ledger(user_id, event_id, rule_id,
trust_delta, intimacy_delta, occurred_at, reverted_at)`，唯一 `(user_id,
event_id, rule_id)`，幂等 `INSERT OR IGNORE`（重复事件不二次加分）。首版规则
映射（14.10）：明确约定 trust+2；明确尊重边界 trust+1；用户自愿真实披露
intimacy+1；被明确接纳的角色披露 intimacy+2；已确认冒犯 trust−2（tension 仍
走旧上限）；拒绝/离线/短句 0（不入账）。正向每维日总量 ≤ +4、负向每维
≥ −6，超限截断。撤销 = 标记 `reverted_at` 并按该笔 delta 反向扣除（clamp）
——加减近似可交换；不做全量基线重放（首版裁剪，见回信）。

**事件分流**：结构性事件（活动完成、P2-03 约定状态机推进）在既有完成路径上
确定性产生并即时入账；对话语义事件（尊重边界/自愿披露/接纳披露）的夜间提取
只产出高置信候选供确认，冒犯仅在明确确认后入账；她在当轮的情绪反应由 P1-03
即时表达，与信任入账时机解耦。夜间提取器与 daily 批处理的接线随 P2-02 交付
（首版裁剪）。

**展示**：`display()` 返回 trust/intimacy/derived_stage/substage，旧
value/fill 取 min 兼容；`AgentState` 增 trust/intimacy 派生字段，阶段计算改
双门槛；P1-01 切片状态视图的 trust/intimacy 从过渡映射切到真源。
unlock 增补 8 个阶段内小档升档时刻（14.10.1，台词取自侧写档案第八节，待终审）。

## 后果

- schema 13→17（16 的 namespace 列在本批之前）；ledger 入 reset 清单与
  E03 关系状态类别；affection_log 全量保留标 legacy，不虚造二维历史。
- `update_affection` 降级后，依赖旧即时涨分的测试改用 `set_affection_absolute`
  或 `apply_relationship_event`。
