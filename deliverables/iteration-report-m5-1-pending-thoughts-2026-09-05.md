# M5.1 · 未完成心事与 Narrative Planner（M5 切片）

日期：2026-09-05  
状态：已完成（M5 退出标准达成）

## 本轮目标

让"用户没说话时她惦记的事"变得确定、可追溯、可观察、可放下——心事由真实事件产生，由 Narrative Planner 决定时机，表达永远走既有主动队列，绝不伪造、绝不盘问。

## 交付

- `pending_thoughts` 表（schema v5，升级前自动快照）+ `backend/core/pending_thoughts.py` 服务：
  - 字段：kind / source_type / source_id / content（叙事素材，无可执行指令）/ earliest_at / expires_at（7 天）/ priority / max_attempts(2) / status。
  - 幂等：UNIQUE(user_id, kind, source_id)，同一来源一生只挂一次心事。
- 确定性生产者（sync_pending_thoughts）：
  - 共读搁置 > 3 天 → "想问问读到哪儿了"（earliest_at = 搁置满 3 天时刻）。
  - 24 小时内有 memory_corrected 事件 → "想确认这次记对了没"（earliest_at = 纠偏 + 2 小时）。
- `backend/core/narrative_planner.py`（轻量版 Planner）：
  - 门控：earliest_at 未到不表达、尝试用尽放弃、过期作废、初识阶段只允许读书类非私人化心事。
  - 表达：initiative.maybe_express_pending_thoughts 每日最多一条、仅空闲时、文案由她的人格卡现场展开、经 enqueue_proactive 队列——额度/冷却/勿扰/原子占位零改动。
- 用户主权与可观测：`GET /api/memory/pending-thoughts`（心事列表 + 状态分布统计），`POST .../dismiss` 放下惦记；来源删除（活动/事实）级联作废心事。
- 既有能力映射进 M5 账面：日常节奏（精力/休息/昼夜/关系季节）与长期研究线（研究课题+报告）此前版本已交付。

## 验证

| 检查 | 结果 |
|---|---|
| 新增 tests/test_m5_thoughts.py | 4 组全过：生产者幂等与时机、Planner 阶段门控与 earliest_at、表达链路（走队列/每日一条/状态落账）、用户放下与来源级联 |
| 后端聚合 | 49/49 通过 |

## 边界说明

- "延迟表达：消化用户分享后再回应"需要持久任务驱动，本轮未做；低频生活事件模板池、精力有限选择、惊喜编排按路线单独立项（M5 进度清单已标注）。
- 7 天连续真实运行的额度观察属部署环境事项，结构上已由复用 enqueue_proactive 保证。

## 收尾

M0–M5 退出标准全部达成。下一个大点 M6「在场感与共同空间」：首批做"我们的角落"（真实 artifact 空间化）与情绪声线；桌面宠物/语音输入需用户拍板。
