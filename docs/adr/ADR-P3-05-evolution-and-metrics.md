# ADR: P3-05 演化日志与本地质量统计

状态：已实现（2026-09-09，执行：ZCode）
依据：docs/Zcode技术指导.md P3-05 + 2026-09-07 用户拍板（白名单仅表达层）

## 背景

人格演化需要可审计：参数随真实事件小步漂移，用户能看懂、能撤销；运行质量需要
可观测，但不能把聊天/日记复制一份当遥测。

## 决策

### A：演化日志（`persona_evolution_log` 表，`core/persona_evolution.py`）

1. **白名单仅表达层三参数**（2026-09-07 用户拍板）：`humor_usage_rate`、
   `verbosity_preference`、`initiative_template_weight`。理由：三参数都能被
   P0-03 eval 直接测量，漂移可被发现，回滚无副作用；身份、安全、隐私、权限
   参数永不入白名单。话题兴趣待 eval 具备话题分布测量能力后再版本化加入。
2. **小步上限** |Δ|≤0.1/步，**冷却** 24h（同参数）；越界 clamp 到 [0,1]。
3. **撤销重算**：撤销只置 `reverted_at`，当前值按「重放全部未撤销且源仍
   active 的演化」推导——不把数值写回很久前的 old 覆盖新变化；源事件删除/
   作废后对应偏移自动失效（`_event_alive` 现读现验）。
4. **无快照列**：当前值是推导值，避免快照与账本不一致的第二真相源。
5. **消费方**：本期只落账本与 API；behavior/幽默选择消费 `current_value`
   的接线放批次 13 内容加量时一并接入（演化先行、消费后接，避免一次改两处）。

### B：本地质量统计（`experience_metrics` 表，`core/experience_metrics.py`）

1. **只记五类聚合计数**：latency（耗时档）/ rule_failure（规则名）/
   repetition（去重命中）/ source_pick（来源 entry id）/ user_feedback
   （明确反馈对象类型）。value ≤120 字，**不含任何聊天/日记正文**。
2. **默认本地**（flag `experience_metrics_enabled`，env
   `FEATURE_EXPERIENCE_METRICS_ENABLED` 可作部署初值）；可关（停写）可清
   （`clear_user`）；临时轮不写（调用方管线 ephemeral 分支不调 record）。
3. **不属于关系包**：不进 `relationship_export` 类别；reset 清空。
4. **不自调参**：统计只产计数视图（复用质量面板既有入口）；一次统计变化
   不改人格；问题候选交人工审查。

## 备选与取舍

- 快照列方案（存 current_value）：查询省一次重放，但引入双真相源与撤销
  边界情况，放弃。
- 统计放 kv（无新表）：无法按天聚合与按 kind 索引，30 天窗口查询退化为
  全量扫描，放弃。

## 后果

- 撤销重算在演化条目多时是 O(n) 重放；白名单三参数每日最多数条，量级可忽略。
- 统计表按天聚合行数可控（5 kind × 活跃 value 数），无清理任务依赖。
