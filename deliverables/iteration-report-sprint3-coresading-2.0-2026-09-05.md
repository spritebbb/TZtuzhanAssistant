# Sprint 3 · 共读 2.0（第一切片）

日期：2026-09-05  
状态：已完成

## 本轮目标

把共读从"一起翻页"升级为"一起留下可追溯的看法"：双方观点分角色保存，读完生成 `reading_finished` 关系事件与共同书摘产物，相关语境下能自然回访，普通聊天保持零污染。

## 交付

- 数据（schema v4，升级前自动快照）：新增 `activity_viewpoints`（user/tuzhan/shared 分角色，UNIQUE(activity_id, role, position)）、`relationship_events`（最小事实层，`WHERE status='active'` 部分唯一索引作幂等键）、`artifacts`（版本化产物）；三表全部进入 `userdb.reset()` 降级分支与 `reset.py` 双清单。
- 观点保存：`save_viewpoint` 强制显式角色，`PUT /api/activities/{id}/viewpoint` 校验 role 枚举；模型观点无法冒充用户观点。completed 后补记的观点落为全书视角（position=-1）。
- 完成闭环：`complete_activity` 同一事务内 ①确定性汇编共同书摘（只汇总真实书签与观点，无 LLM 参与，诚实降级：无记录时如实说明）写入 artifacts（版本 +1），②幂等写入 `reading_finished` 事件（payload 记录 filename/进度/书签数，confidence=1）。
- 回访：`active_reading_context` 在没有在读活动时，取 45 天内最新 `reading_finished` 事件对应书摘作回访素材；入口仍是既有阅读意图正则，非相关话题注入为空；引用带不可信声明。
- 删除级联：`knowledge.delete_document` 调 `forget_activity_data`——观点与书摘随源删除，事件作废 forgotten，不留幽灵回忆。
- 前端：共读面板「各自的看法」分角色编辑区（讨论草稿自动带上用户观点原文）、完成后的共同书摘卡片、「一起读过」列表可点开回看书摘；`ReadingActivity` 类型同步 viewpoints/summary 字段并对旧数据防御。

## 验证

| 检查 | 结果 |
|---|---|
| 后端聚合 | 45/45 通过（test_activities 扩至 7 组：生命周期/pipeline 注入/观点·事件·书摘·幂等·回访/API/reset/两次删除级联；test_schema_backup 断言 v4 与新表） |
| 前端 Vitest | 8 文件、24 测试通过（ActivityPanel 新增观点保存与书摘回看 2 例） |
| TypeScript | `vue-tsc --noEmit` 通过 |
| 生产构建 | Vite Web 与 Electron 产物均通过 |
| 浏览器 E2E | 5/5 通过 |

## 风险与边界

- 事件类型目前只有 `reading_finished`；`promise_completed` / `important_date` / `memory_corrected` 与 pending_thoughts、Narrative Planner 属 Sprint 2，未在本轮混做。
- 共同书摘为确定性汇编，不生成模型式总结文案——这是有意为之（可追溯、不虚构），后续如需"保留分歧的书摘"，在 Sprint 2 叙事层再做。
- 跨人格隔离依赖现有 persona_profiles 的 user_id 命名空间，本轮新增表均带 user_id 作用域，未做额外人格矩阵回归。

## 收尾

Sprint 0/1/3 完成，Sprint 2 关系事件内核是下一个独立切片。
