# Sprint 2 · 关系事件内核（第一切片）

日期：2026-09-05  
状态：已完成

## 本轮目标

让过去真实发生的事变成可追溯、可过期、可作废的关系事件，并只在聊天内的相关语境自然回忆——不做任何新增主动推送。

## 交付

- 统一事件服务 `backend/core/relationship_events.py`：
  - `EVENT_TYPES` 类型注册表，未注册类型拒绝落库；首批 4 类：reading_finished / promise_completed / important_date / memory_corrected。
  - 幂等：`user_id + event_type + source_id` 部分唯一索引，重复写入直接忽略。
  - 过期：`expires_at` 过滤，`active_events` 只返回 active 且未过期的事件。
  - 纠正：`mark_corrected` 把事件标记为 corrected（发生过，但记录需要修正）。
  - 级联：`invalidate_for_source` 按来源作废；删事实（含级联候选）与删共读活动都会触发。
  - Sprint 3 的 reading_finished 直写 SQL 与级联 SQL 收口进本服务，单一权威。
- 三个新生产者：
  - `promise_completed`：C6 约定到点被跟进并标记完成时（`initiative.maybe_follow_up_promise`）记录。
  - `important_date`：特殊日子到来时（pipeline 4.1）记录；每年重复的日子用 `refresh_on_conflict` 刷新同一条事件的发生时间与 7 天有效期，不逐年堆积。
  - `memory_corrected`：管理页改写 / 冲突确认经 `fact_lifecycle` 留痕（old/new 内容入 payload）；对话内 LLM 仲裁真删不落事件——事实本体已随纠正消失，留事件即幽灵回忆。
- 聊天内自然回忆（唯一表达出口）：`event_recall` 仅在约定相关话题（约定/答应/说好等）或直接点名某个日子（标签命中）时注入真实事件素材；无关话题返回空。pipeline 注入位置与共读上下文同级，引用带克制指令（不复述、不清单腔、不生硬转话题）。
- 解释快照：事件回忆被使用时，回复解释面板新增「事件来源」条目（如"约定事件：用户答应周五发 demo"），回答"因为哪件真实发生的事"；不暴露思维链。

## 验证

| 检查 | 结果 |
|---|---|
| 新增 tests/test_relationship_events.py | 5 组全过：类型注册与幂等、过期/纠正/作废、memory_corrected 生命周期与级联、回忆语境门控、pipeline 接线 + 解释快照 |
| 回归 test_activities / test_memory_correction | 全过（reading_finished 收口与删事实级联未破坏原行为） |
| 后端聚合 | 46/46 通过 |
| 前端 | 本轮零前端改动，未重建（契约未变） |

## 风险与边界

- `pending_thoughts` 与 Narrative Planner 属 M2 剩余增量，本轮未混做。
- 约定回忆窗口 60 天、日期事件有效期 7 天，均为常量，后续可在真实运行中调整。
- promise_completed 的语义是"约定到点被跟进并标记完成"（与 C6 mark_promise_done 同步），不是用户口头确认"我做到了"——后者需要对话理解，留给后续切片。

## 收尾

Sprint 0–3 全部完成。下一轮按 TECH-PLAN 第 9 节在 M4「冲突与修复」与 M5「自主生活」之间重新拍板。
