# 体验加量实施总纲（全路线完整版升级）

> 规划：GLM（ZCode），2026-09-08 经用户拍板：**不留最小实现，把《Zcode技术指导》中全部未实现路线按完整设计+体验加量标准一次性排定并实施**。
> 本文档是执行总纲：批次划分、依赖序、每批次的完整验收标准。实施过程中每完成一批次，把状态与提交号回写本文档状态表。
> 约束继承：`docs/Zcode技术指导.md` §13–§14 全部工程契约（LC-1/JOB-1/OUT-1/QA-1、单主题提交、VERIFY 标记、reset/导出覆盖、schema 迁移纪律）。

## 0. 范围裁定：什么算「未实现」

以技术指导 §22 完成账本为基线逐节核对后，未实现项分三类：

| 类别 | 内容 | 处理 |
|---|---|---|
| A. 方案完成、零代码 | L01–L08、L16、G01–G04、F01–F07、P3-05、§17.1–17.3 | 本总纲全部纳入，完整版实施 |
| B. 已实现但「密度不足」 | 日程素材池（已完成 07498a2）、触发阈值、已完成功能的内容扩量 | 并入相关批次做加量 |
| C. 明确不采用 | SillyTavern 卡兼容、Neo4j、MaiBot 主循环、外部记忆第二权威库 | 不做（§17 明确排除） |
| D. 外部条件未齐 | P3-03 训练（GPU）、P3-04 真实迁移（PoC+审查前置）、L09 STT（模型）、L10 桌宠（真机）、L12 域名、L13/L14 账号、L15 群聊 | 代码可离线交付的部分照做（adapter/协议/测试），真机/账号/训练环节留运行记录空位，按 §14 纪律标注，不宣称完成 |

已核对实现状态的关键锚点（2026-09-08 工作树）：schema v22；P1-01~05、P2-01~06、P3-01/02、M0~M8 已落库；14.10.1 小档（`substage_of`/unlock 升档时刻/切片谓词）已实现；昨天的体验收口四片（开关面板/她的观点/心事面板/状态行）与日程加量（07498a2）已落库。

## 1. 批次总览与依赖序

依赖原则：数据底座先行（表/schema）→ 核心引擎 → 消费接线 → 前端露出；能并行验收的切片独立提交。每批次一个独立主题提交，全部带 VERIFY。

```
批次1 L06 生活模板池 ──┐
批次2 L03 关系气质 ────┤
批次3 G01 记忆显著度 ──┼──> 批次7 F02素材互通（消费各源）──> 批次8 F05/F06/F07（露出层）
批次4 F01 问候 ────────┤
批次5 G03 悬念 ────────┤
批次6 G04 求助 ────────┘
批次7 F03 心事门控 + L04 幽默记忆（registry 消费层，依赖批次1-6的 provider 模式）
批次8 F04 专注收尾 + F05 共读C + F06 意图预填 + F07 记忆露出（体验接线层）
批次9 L01 网页/EPUB 摄入（F05 阅读地图受益方）
批次10 L02 世界观/角色共创 + 观察日志（依赖 F06 的活动壳扩展）
批次11 L05 领域信任 + L07 审美/房间 + L08A/B presence 统一与 UI 收纳
批次12 L16 共享资源 + P3-05 演化/统计 + §17.1 表达必要性 + §17.2 学习管线 + §17.3 工具加固
批次13 收尾加量：触发阈值放宽（需用户数值拍板）+ 各已完成功能内容扩量清单执行
```

## 2. 各批次完整验收标准

> 每批次 = 完整设计条款逐条落地 + 下列验收 + 全量聚合绿。不做「最小版」；设计文档中「首版 6 个」「初值 0.25」等参数按设计原文执行（那本身就是完整规格），不缩减覆盖面。

### 批次1：L06 生活模板池与精力有限选择（完整版）

- **数据**：`data/persona/<persona>/life_templates.json`（schema=1：`{id,location_id,prerequisites,energy_cost,weight,output_kind,cooldown_days}`），首版 ≥8 个模板，全部来自正典（含 C-02 蕾拉下午、P-00 城市、C-01 蛲蛲联机）；加载校验+无效模板 fallback rest+日志。
- **引擎**：新建 `core/life_templates.py` + `choose_life_event(user,now)`；挂 `advance_schedule` 日切换点（与现有 daily_life 素材互补不互斥：模板命中当天产出 `kind='outing'` 事件）；候选每天最多 1、每模板 7 天冷却、固定种子概率 0.25；精力来源=state 派生精力，<30 只允许 rest/quiet_reading，≥30 才 outdoor/research，扣能量只在事件提交时一次；无合法模板保持沉默。
- **融合**：与真实天气融合时保留天气 source id，虚构不冒充用户所在地事实（铁律 2）。
- **露出**：状态行（`/api/presence`）消费 outing 事件；`current_activity` 在外出时段返回模板 location。
- **reset/导出**：事件入既有 `character_life_events`（reset 已覆盖）；kv 模板冷却状态登记 kv_registry，runtime 不导出。
- **测试**：新建 `tests/test_life_templates.py`——低能量不出高能外出、同日 tick 同结果（幂等）、7 天冷却、停用模板不抹历史但不再引用、无效 JSON fallback、概率确定性（同种子同结果）、与 daily_life 同日共存不超限。
- **VERIFY**：`test_life_templates.py ;; test_time_tick.py ;; test_state_interaction.py ;; test_presence_api.py`

### 批次2：L03 关系气质（完整版）

- **数据**：新表 `relationship_style_evidence(user_id,event_id,style,weight,occurred_at)` 唯一 user/event/style；LC-1 长期关系类别（导出+恢复重映射+reset）。
- **引擎**：新建 `core/relationship_style.py`：五 style（companion/playful/confidant/growth/romantic）；只吃明确事件（共同活动→growth、互相理解→confidant、确认喜欢的梗→playful、romantic 受双维阶段约束）；90 天窗、30 天半衰期权重；`derive_style(user,now)->{style_ids,来源简述≤2,valid_until}`，不向 UI 返回分数；≥3 个不同日期有效事件才显示，否则「还在慢慢形成」；最高两类权重差<10% 可并存。
- **接线**：唯一 reducer 挂 `relationship_events.record`（与 L05 共用入口约定）；自动倾向对 behavior 单轴 ±0.1（clamp，不覆盖显式偏好与阶段边界）；「不要这种气质」走 P2 偏好屏蔽（P2-02 resolver 新 category）。
- **API/前端**：GET `/api/memory/relationship-style` + DELETE；「我们之间」区展示气质标签+最多 2 条来源短句；与 14.10.1 小档展示同区（阶段+小档+气质）。
- **测试**：`tests/test_relationship_style.py`——单日高频不形成分支、离线不惩罚、源删重算、低亲密无 romantic、半衰期确定性、导出恢复、屏蔽后 derive 排除。
- **VERIFY**：`test_relationship_style.py ;; test_m4_relationship.py ;; test_relationship_bundle.py`

### 批次3：G01 记忆显著度（完整版，四片按序独立提交）

1. **评分影子模式**：`core/memory_salience.py` + `memory_policy(fact_id,user_id,tier,score,score_version,review_at)`；评分公式 S=40×explicit+20×anchor+10×min(days,3)+10×first_event（§14.8 原文）；shadow 记录不改行为。
2. **生命周期接线**：检索/过期策略按 tier 分流（同 fact id 单一主存，向量只是索引）；升降级幂等、pinned 永不自动降级；B01 契约回归。
3. **视角与初历**：`memory_annotations` 侧表（owner=assistant、source_fact/event、origin/confidence、不改写事实文本）；初历 user_id+event_type+主题键唯一。
4. **露出**：解释层消费 tier/annotation；「可见遗忘」非敏感元数据范围在 ADR 说明。
- **VERIFY**：`test_memory_salience.py ;; test_memory_correction.py ;; test_memory_v2.py ;; test_ephemeral_privacy.py`

### 批次4：F01 问候去模板（完整版）

- `core/greeting_material.py`：`collect_greeting_material`（只取授权真实事件/记忆/生活事件）+ `choose_greeting_variant`（四类×≥3 方向模板，资源入人格切片目录）；`greeting_variant_usage` runtime 登记；greeting/initiative 双接入；LLM 失败诚实短回退；生成期用户已发言则丢弃。
- **VERIFY**：`test_greeting_material.py ;; test_greeting.py(如存在) ;; test_ephemeral_privacy.py`

### 批次5：G03 悬念/开放问题（完整版）

- `open_questions` 全状态机（open/researching/resolved/dismissed/expired）；触发=用户明确想继续查或证据不足；7 天到期、最多 2 次复查、间隔 24h；JOB-1 求证；evidence_hash 规范化（canonical_url+title+摘录）；无新证据不发假进展；表达经主动仲裁；LC-1 导出。
- **VERIFY**：`test_open_questions.py ;; test_m5_thoughts.py ;; test_proactive_arbiter.py`

### 批次6：G04 求助与欲望（完整版）

- `companion_requests`（song_choice+book_choice 两类全做）；intimacy≥50 且 trust≥50 门控、7 天至多一次、24h 过期；拒绝即结束不扣分；接受后写角色活动产物（虚构命名空间）；GET list + POST respond API + 聊天意图同函数；LC-1。
- **VERIFY**：`test_companion_requests.py ;; test_proactive_arbiter.py ;; test_persona_eval.py`

### 批次7：F03 心事门控 + L04 幽默记忆 + F02 素材互通（完整版，三片独立提交）

- **F03**：`context_candidates()` + registry thought provider + `thought_context_receipts`（唯一三元组、runtime）；200 字上限、冷却 4 回合、临时轮不写 receipt。
- **L04**：`humor_usage` 侧表（candidate/approved/retired）；positive/negative/unknown 明确反馈语义（「哈哈」只 unknown）；7 天 2 次 positive 才 approved；「别玩这个梗」立即 retired；严肃场景默认不插；delete_term 级联；LC-1。
- **F02**：`source_links` + `core/narrative_sources.py`（resolve/collect，深 2 总 5 项 1200 token）；daily/surprise 两条消费线；三 namespace 编译隔离。
- **VERIFY**：三片各自测试 + `test_ephemeral_privacy.py ;; test_relationship_bundle.py`

### 批次8：F04 + F05 + F06 + F07（完整版，四片独立提交）

- **F04**：`build_wrapup_material`（真实时长/中断/目标/行为帧，≤100 字）+ `wrapup_outbox`（唯一 user/activity、重试 2 次、delivery_id 去重、不占主动额度语义保留、API 超时不回滚）。
- **F05**：`reading_map.py` 两表全量（segments/bookmarks 状态机）、finish 唯一解锁、OUT-1 草稿→用户确认、全书确定性汇总（省略计数）、legacy_position 迁移、hash 409、源删级联。
- **F06**：`activity_drafts.py` + 签名短期草稿引用（20 分钟、重启过期）+ confirm 幂等 receipt + 活动互斥告知；卡片化前端一并做（用户已拍板完整版）。
- **F07**：解释 JSON 可选键 + 二次授权 + 气泡折叠展示 + 版本化编辑 + 410/404 处理；无新表。
- **VERIFY**：四片各自测试 + `npm --prefix frontend test`（F05/F06/F07 含组件测试）

### 批次9：L01 网页/EPUB 摄入（完整版）

- `core/document_import.py`：URL 导入（SSRF 全边界：禁 loopback/private/metadata、DNS 每跳校验、15s 超时 3 跳）+ EPUB（OPF spine 顺序、防解压炸弹/外部实体）；`document_segments` 表；kb_documents 加列（source_url/source_hash/parser_version）；202 job_id + 可取消 worker；同源 hash 去重/版本；LC-1 + 源删级联。
- **VERIFY**：`test_document_import.py ;; test_knowledge_base.py ;; test_activities.py`

### 批次10：L02 世界观/角色共创 + 观察日志（完整版）

- writing 壳 subtype=story/world/character + `structured_outline_json` + propose/confirm outline；observation 活动壳 + `observation_entries`（observer/来源/置信度）；完成产物 co_world/co_character/observation_log artifact 白名单；虚构排除器更新；LC-1 + 活动壳互斥；feature 关只停新增。
- **VERIFY**：`test_creative_extensions.py ;; test_cowriting.py ;; test_relationship_bundle.py`

### 批次11：L05 + L07 + L08（完整版，各自独立提交）

- **L05 领域信任**：五域事件+snapshot+唯一 reducer（与 P2-01/L03 共用 `apply_relationship_event`，禁止双算）；首次领域值=全局 trust；日 ±4 clamp；GET/DELETE API；行为调制限披露/求助/玩笑强度。
- **L07 审美/房间**：`aesthetic_preferences` + `resolve_aesthetics`（明确要求>用户偏好>角色偏好，最多 3 token 带 source id）+ `artifact_placements`（无源物件不显示、隐藏/重排不删源、LC-1 重映射）+ API/展示。
- **L08A**：`core/presence.py` 统一 VisualState 后端输出 + meta 整合；**L08B**：前端 visualState store + 工具入口收纳「更多」弹层 + 拍板四项（好感度条移出聊天/状态灯异常才显示/不留痕收纳/解释快照保留）+ 首屏 ≤12 元素验收 + 可达性回归。
- **VERIFY**：各自测试 + `npm --prefix frontend test ;; npm --prefix frontend run build`

### 批次12：L16 + P3-05 + §17.1–17.3（完整版，各自独立提交）

- **L16**：`shared_resources`/`resource_grants` 两表 + 只读授权校验 + registry 只拿授权片段 + 导出 ACL 清单导入收紧。
- **P3-05**：A 演化日志（白名单 3 参数、小步上限、冷却、撤销重算）+ B 本地质量统计（延迟/失败规则/重复率/来源选择/明确反馈；默认本地、可关可清）+ 独立 ADR + reset/导出策略。
- **§17.1**：`expression_policy`（necessity 五因子 0..1、主动候选 ≥0.6 门、用户消息永不门控）+ `attention_state`（≤5 主题、+0.35/×0.7 衰减、<0.1 删除、临时轮内存态）。
- **§17.2**：`learning_pipeline` 三类候选（表达偏好自动确认低风险/术语与行为解释聊天内确认/BatchGate）+ 30 天过期 + 确认后走 P2-02/词汇表/P3-05 白名单。
- **§17.3**：工具 schema registry（权限域/幂等/超时/字节上限）+ 熔断（8 次/轮、同签名 2 次、32KB 预算）+ 会话 LRU（32 会话 30 分钟）+ 取消 token 贯穿 + MCP 断路器。
- **VERIFY**：五片各自测试 + `test_pipeline_scenario.py ;; test_agent_session.py`

### 批次13：触发阈值与内容加量收尾（需用户拍板数值后执行）

- 主动消息：首日可触发的分层阈值（新用户前 3 天 _IDLE_HOURS 降至可体验值，如 2h；维持「初识不主动」底线）；惊喜三重门放宽（14→7 天、概率 35%→50%、空闲 240→120 分钟）；快照 30 天里程碑首月即达。
- 已完成功能内容扩量执行清单：问候变体池补写、升档台词 8 条核对、日程素材随正典扩充机制（新正典条目→素材桶映射表）。
- 每项数值改动前在信箱列明，用户拍板后写入 config/env。

## 3. 执行纪律

1. 每批次独立提交、独立 VERIFY；全量聚合（`pytest tests/` + vue-tsc + vitest + build）在批次 7/12 两个节点与最终节点跑，批次内跑针对性测试。
2. 新表必同步：`_SCHEMA`+`_SCHEMA_VERSION` bump、reset 清单、`relationship_export` 类别、`kv_registry` 登记、`schema_backup` 版本守卫测试。
3. 遇设计冲突/参数疑义：先查 §14/§18/§21 契约；确无先例的在提交信息与信箱标注「自行补充的决策」及理由。
4. 阶段性信箱汇报：批次 3、7、12 完成后各汇报一次（含提交号与测试结果），最终批次后总汇报。
5. 外部条件缺口（批次 D 类）：交付代码+测试+运行记录空位说明，不宣称真机/训练完成。

## 4. 状态回写表

| 批次 | 状态 | 提交号 | 日期 |
|---|---|---|---|
| 1 L06 | 待开工 | — | — |
| 2 L03 | 待开工 | — | — |
| 3 G01 | 待开工 | — | — |
| 4 F01 | 待开工 | — | — |
| 5 G03 | 待开工 | — | — |
| 6 G04 | 待开工 | — | — |
| 7 F03+L04+F02 | 待开工 | — | — |
| 8 F04+F05+F06+F07 | 待开工 | — | — |
| 9 L01 | 待开工 | — | — |
| 10 L02 | 待开工 | — | — |
| 11 L05+L07+L08 | 待开工 | — | — |
| 12 L16+P3-05+§17 | 待开工 | — | — |
| 13 阈值与内容加量 | 待用户拍板 | — | — |

> 前置已完成：日程素材加量（07498a2）、体验收口四片（028034d/2b34e90/76f320b/a4a38e9）、presence 标签修复（e4e2e9f）。
