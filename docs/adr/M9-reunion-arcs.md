# M9 ADR：P2-06 重逢弧持久状态

执行：Codex，2026-09-08。

## 决策

P2-06 使用 `reunion_arcs` 表保存久别重逢的状态机。弧只引用 P1-04 已完成的
`character_life_events.id`，不复制问候、用户回复或离线叙事原文。问候和回复仍写入
权威 `messages` 表，分别以 `offered_message_id` 和 `response_message_id` 关联。

状态为 `pending -> offered -> responded -> closed`；用户第一句明确换题时直接
`offered -> closed`，超时为 `expired`。状态推进不改变信任、亲密或旧好感兼容值。
同一人格和来源唯一，且七天内最多建立一条弧。

## 迁移与生命周期

`bot.db` schema 从 v20 升到 v21。新表对旧用户为空，不回填历史重逢。表进入两套
reset 清单，并随关系包 `life` 类别导出。导入时来源生活事件先重映射，重逢弧一律
转为 `closed` 且清空消息 ID，避免恢复旧关系包自动触发一段已发生的重逢。

来源生活事件被删除后，仍处于 `pending/offered` 的弧在下一次读取时关闭。没有合法
来源时不建立弧，久别问候只能使用中性兜底，不编造角色或用户的离线经历。

## 回滚

代码回滚可停止创建和消费新弧；SQLite 保留 v21 表不会影响旧读取路径。需要彻底回退
时可在备份后删除 `reunion_arcs`，其内容均为可丢弃的运行状态，不影响消息和生活事件。
