# M9 全项目技术流程预研报告

执行：ZCode（GLM），2026-09-07。基线 48678ba＋当日未提交工作树。
方法：直接源码勘察（子代理额度受限，未使用）；与《Zcode技术指导》的假设逐项核对。
用途：P0-01A 及后续切片的开工依据；审查批的背景材料。**纯只读预研，未改任何代码。**

---

## A. 应用骨架与运行时数据流

### A.1 启动序列（backend/app.py:59 create_app）

- 中间件三道：host guard、origin guard、remote auth guard（app.py:79/102/142）
- 40 个路由注册（app.py:172-203+），生产环境后端直接服务前端构建产物，Electron loadURL(127.0.0.1:8801)
- `_startup`（app.py:~223）：persona 文件存在性检查（降级告警）→ 向量库维度锁校验（防哈希回退向量写入已锁 1024 维 collection）→ embedding 后台预热（600s 超时，不阻塞启动）→ 维护循环 `maintenance_loop` 常驻
- **P1-04 相关**：启动即挂维护循环，time_tick 的「app 启动补跑」有现成挂点；`reset_epoch` 机制已在 reset.py 落地（见 B.3）

### A.2 API 面（backend/api/，40 文件）

sessions / chat（SSE 流式＋轮询双通道）/ confirm / activities / artifacts / dashboard / diary / focus / future_letters / goals / writings / relationship（+snapshots+versions）/ dual_perspectives / sealing / possibilities / colists / memory_admin / keepsake / knowledge / unlocks / usage / agent / vision / images / meta / personas / audit / remote / config_api / tts / greeting / mcp_servers / plugins_api / health / user_reset。
- 与文档假设一致：无独立功能面板的约束下，F 系列全部有现成路由落点；L08 的 meta 路由存在。

### A.3 主动消息链路（backend/core/initiative.py，结构完整）

`_eligible_users` → `_build_proactive_prompt`（F01 要改的函数，:137）→ `generate_proactive_message`/`generate_proactive_content`（含主动生图 :228）→ `_tick_once`（:281）→ `_arbited_proactive`（:571，P0-01B 出口之一）→ `enqueue_proactive`/`dequeue_proactive(_message)` / `subscribe`/`_notify_subscribers`（SSE 推送）/ `poll_message_for` / `poll_for`；`initiative_loop`（:484）常驻；`set_deliver_hook`（:358）外部投递钩子；`_arbitrate_secondary` → `_maybe_surprise`。
- **P2-04/P2-05 相关**：额度仲裁与投递全部经此文件，链式事件/追发接入点明确。

### A.4 插件与工具系统

- 插件源码目录是**仓库根 /plugins**（loader.py:45 `PLUGINS_DIR`），不是 backend/plugins/（后者只有 loader/context/`__init__`）；`load_all_plugins`（:348）＋热更新扫描 `_scan_watch`（:368）；钩子 `plugins/context.py:apply_reply`（P0-01A 已锁定其在流式后执行的缺陷）
- 工具：`tools/builtin/register_all.py`（内置仅 memory）＋插件注册；`tool_loop.run_tool_loop`（:339）/`_run_native`（:389）签名与文档假设一致（P3-02A 复用落点确认）

### A.5 LLM 层（backend/core/llm.py，P0-04 现状）

- 双客户端：`get_client()`（主）＋ `get_perception_client()`（感知槽位覆盖：LLM_PERCEPTION_MODEL/BASE_URL/API_KEY，缺省回落主客户端）；VISION_* 三元组独立（M9-00）
- 重试：`_MAX_RETRIES=2`、退避 1.5s、仅 TimeoutError 可重试；`_record_usage(channel, model, ...)` 已有按通道记账（P0-04 用量归属可复用）
- **无任务路由机制**（现状与文档假设一致，P0-04A 从零建 `model_routes.py`）
- config 无 `FEATURE_*` 先例，但有同构 env 开关（STICKER_ENABLED/MEMORY_V2/SEARCH_ENABLED/PROACTIVE_*），P0-01 起新增 `FEATURE_<语义>_ENABLED` 风格一致

## B. 数据层全貌

### B.1 bot.db（backend/core/userdb.py，`_SCHEMA_VERSION=13`）

33 张表：users / messages / long_memory（含 pinned）/ affection_log / mood_log / facts / user_meta / kv_store / important_dates / stickers / user_profile / user_terms / user_style_map / diary / research_reports / promises / usage_log / triples / tasks / kb_documents / kb_chunks / activities / activity_notes / activity_viewpoints / activity_goals / goal_progress / activity_writings / writing_turns / activity_lists / list_items / future_letters / relationship_events（幂等唯一索引 idx_relationship_events_idem）/ relationship_snapshots / dual_perspectives / relationship_versions / pending_thoughts / artifacts / unlocks。
- relationship_events 已有 confidence/privacy/occurred_at/expires_at/status（forgotten/corrected）——P2-01 ledger 与 P2-04 链实例有现成幂等模式可循
- **promises 列现状**：user_id/content/follow_up/source/created_at（userdb.py:1548）——无 owner/due_at/status，P2-03 迁移量确认

### B.2 其他两库

- sessions.db：sessions/messages/archives（backend/session/store.py:65-83，归档含 messages_json）
- agent_tasks.db：agent_tasks（backend/agent/session.py:76）
- 跨库写点：maintenance/loop.py（已勘察）＋各自模块；P1-04 `job_runs` 建议落 bot.db 与 §17.2 一致

### B.3 reset 与世代（backend/core/reset.py）

`reset_epoch()` / `epoch_is_current(epoch)` / `user_write_guard(epoch)` / `_quiesce_user_writers()`（120s 静默等待）/ `reset_everything()`。
- **P1-04 关键结论**：进程内 epoch 守卫已存在；跨进程 tick 必须在写路径校验 epoch（文档 14.5/P1-04 第 4 条的「reset 时旧 epoch 不写回」有现成机制可接，但**只覆盖本进程任务**，Windows 计划任务进程需走数据库侧校验）

### B.4 权威路径（全部核实存在）

- fact_lifecycle：`delete_fact_everywhere` / `update_fact_everywhere` / `resolve_fact_conflict_everywhere`（含向量删除与纠正事件）
- relationship_export：类别化导出（identity/memory/milestones/life/tasks/activities…）/ `preview_restore`（引用完整性校验＋跨类别引用检查）/ `restore_bundle`（拓扑序＋向量重建调度）
- kv_registry：`match_spec(key)` 规格查询
- memory/ 子系统 13 模块（fact_extractor/long_term/short_term/triple/topic/compress/migration/engine/manager/vector_store/embedding…）

### B.5 affection 消费者（P2-01 迁移波及面）

daily / perception / greeting / persona / state / pipeline（六处直接消费）——与文档「先列全部消费者」要求吻合，P2-01 开工时逐一核对。

## C. 切片落点核查表

| 切片 | 落点 | 结论 |
|---|---|---|
| P0-02 | maintenance/schema_backup.py | ✅ 存在；tests/test_schema_backup.py ✅、test_relationship_bundle.py ✅；test_periodic_backup.py ❌（待建，符合预期） |
| P0-03 | evals/persona.py | ✅ 硬红线（_PARTICLES/_BOILERPLATE/hard_violations）＋确定性评测＋裁判合并（硬红线否决权）；persona_cases.json 48 案，现有字段 id/tag/stage/affection/user/reference/intent/forbidden/max_chars/rubric_any——**无 signature_ids/multi_turn/must_not**（P0-03 增量字段确认）；scripts/run_persona_eval.py ✅ |
| P0-04 | llm.py 客户端 | ✅ 双客户端＋感知覆盖＋通道记账；无路由（待建，符合预期） |
| P1-01 | persona.py:build_system_prompt（:82） | ✅；persona_profiles.active_card_path（:222）✅ |
| P1-02 | colists.list_context（core/colists.py:277） | ✅ 签名 (user_id, query) 与文档一致 |
| P1-03 | behavior.build_behavior_frame（:252） | ✅ 接 AgentState＋season_line |
| P1-04 | daily.run_daily_batch（:112）/write_daily_diary（:195）/maybe_write_research_report（:232）/extract_promises（:267） | ✅ 全部存在 |
| P2-03 | initiative.maybe_follow_up_promise | ✅（文件内，随主动链路核实） |
| P2-06 | offline_narrative.collect_offline_context / greeting.greeting_for（:158） | ✅ |
| P3-02 | tool_loop/run_tool_loop(:339)/_run_native(:389) | ✅；intent/search/knowledge 模块存在 |
| F01 | greeting._greeting_text（:85）/initiative._build_proactive_prompt（:137） | ✅ |
| 前端 | components 17 个（ChatView/ChatInput/MessageBubble/MemoryPanel/ActivityPanel/Portrait/ToolBar/SettingsPanel…） | ✅ F05/F06/F07/L08 组件落点齐 |
| 新建模块 | model_routes/output_hygiene/context_registry/emotion_state/schedule/time_tick/user_preferences/event_chains | ❌ 全部不存在（符合「建议实施接口」定位，无重做风险） |

## D. 与文档假设的偏差汇总

1. **无实质冲突**：任务书点名的全部既有函数/表/文件与实际一致；七个「新建」模块确认不存在。
2. 小勘误：插件源码目录为仓库根 /plugins（非 backend/plugins/）——P0-01A 插件钩子分析时注意 import 路径即可。
3. persona_cases.json 现有字段不含 P0-03 计划的 signature_ids/multi_turn/must_not/锚点——按计划增量添加，48 案原样加载兼容。
4. promises 表比 P2-03 目标少 owner/due_at/status/action_kind——迁移量已量化。
5. reset 的 epoch 守卫只保护进程内任务；P1-04 跨进程 tick 需数据库侧 epoch 校验（新增设计点，ADR 覆盖）。
6. **【增补篇修正】动态功能开关系统已存在**：`backend/core/features.py`——data/feature_flags.json 持久化、`flag(name)` 读（5s 缓存）、`set_flag(name,value)` 原子写（写锁＋白名单 FLAG_DEFAULTS，当前仅 profile_enabled，pipeline 三处消费）；注释明确预留「未来功能开关面板的写入端」。此前「config 无 FEATURE 先例」表述不准确：env 静态开关（STICKER_ENABLED 等）与 JSON 动态开关两层并存。**待 Codex/用户定夺的设计点**：§13.6 说 FEATURE_*_ENABLED 集中在 config（env 层），§18 拍板「用户在设置区逐个手动开启」（指向 features.py 动态层）——建议口径：部署级默认走 env，用户可调开关登记 FLAG_DEFAULTS（默认 False），每片任务书里写明走哪层（P0-01A 起即需此决定）。

## G. 自审与缺陷自查（2026-09-07 第三批，应 Codex 审查前自查要求）

### G.1 B01 已知薄弱点（诚实清单，供审查聚焦）

1. **测试的向量断言不含「部分失败」路径**：`_VecRecorder` 恒返回成功；真实 `delete_many` 部分失败时 SQLite 仍继续删行（except 吞掉）——该「向量失败不阻断 SQLite」行为是继承旧实现的设计决策，本片未改、也未测。孤儿向量由重建流程兜底的声明沿用旧注释。
2. **`clean_old_long_memory` 的 keep 语义是全局非 per-user**：全局保留最近 keep 条 unpinned，活跃用户 A 可把用户 B 的记忆全部挤出配额——旧实现同样如此，本片未改（任务书范围仅 pinned 契约）；记录在案，若要 per-user 配额属新切片。
3. **row_factory 修复的回归风险**：`conn.row_factory = sqlite3.Row` 只设在本函数连接上；同模块其他函数（backup/checkpoint 等）连接方式未动，无影响。测试实证两用户路径正常。
4. **跨人格误删的测试假象**：测试 UA/UB/UC/UD 直接用裸 user_id 插行，真实人格命名空间是 `assistant-main::persona::{pid}`（persona_profiles.scoped_user_id:186）——清理按 user_id 分组，命名空间形态不影响 SQL 行为；测试仍有效，但审查时应知测试 id 与生产 id 形态不同。
5. **pin 的写入方仅 memory 工具**（tools/builtin/memory.py，pinned=True 显式记住）与 facts 表的 update_fact_pinned（api/memory_admin.py:132）——facts 表的 pinned 由 fact_decay 的到期边界独立处理（pinned=1 永不自动衰减，fact_decay.py:16-23），两表各自成立，B01 只覆盖 long_memory。

### G.2 文档批自查

1. §14.1 侦查段的行号基于当日工作树，已注明「重构后失效」；若 Codex 审查时先行修补其他文件，行号漂移属预期。
2. 技术指导 §17.2 写「P1-04 job_runs 建议 bot.db」，预研 F.4 补充了 `_claim_running` CAS 先例（agent_tasks.db 侧）——两者不矛盾（job_runs 落 bot.db、CAS 模式照抄），但 ADR 时须把「agent_tasks.db 有既有 CAS 先例」写进去，避免审查误以为跨库参照。
3. 预研 §A.5 说「config 无 FEATURE 先例」，§F 后又发现 core/features.py 动态开关——已在 §D6 修正并上升为口径分歧待定夺项；审查时以 §D6 为准。
4. 世界正典 §5 说「她的纪念日不向用户索要庆祝」与 P1-05 文档「预热 2-3 天产生候选」兼容，但 P1-05 落地时 character_fiction 类纪念日是否走 important_dates 表（现有表为 user_real 语义）是未决点——本预研未展开，P1-05 开工时补。

### G.3 审查不到位风险自评

最高风险项＝G.1.2（keep 全局语义是否算缺陷）与开关口径分歧（§D6）；两者都已显式列出而非隐埋。文档批的隐性风险是「拍板记录与文档既有条款矛盾」（G.2.2/G.2.3 两处），已自纠一处、上报一处。回归佐证：test_memory_v2.py 11/11 通过（pinned 到期边界语义未受 B01 影响）。

### G.4 第四批补查（终版，2026-09-07）

1. **pinned 契约的完整覆盖面确认**：long_memory 的容量清理共两个入口——维护循环 `clean_old_long_memory`（B01 已修）与 pipeline 每轮的 `db.prune_long_memory(user_id, keep=_LM_MAX_ROWS=800)`（pipeline.py:1693）。后者 SQL 本就只删 `pinned=0`（userdb.py:994），pinned 契约天然成立，B01 无需动它。long_memory 的 pinned 写入方全库仅一处：tools/builtin/memory.py:79（pinned=True 显式记住）。**结论：B01 修复后，两入口＋一写入方，pinned 契约闭环**。
2. **`_LM_MAX_ROWS=800` 与维护循环 `LONG_MEMORY_KEEP=2000` 双阈值并存**：pipeline 每轮按人清到 800（pinned 不计），维护循环全局清到 2000——语义上是「活跃即时清理＋兜底巡检」，但 800/2000 两个数字无文档解释；归入 G.1.2 同一议题，若裁决 per-user 配额则一并对齐。
3. **聚合器规模确认**：test_suite_runner 收集 71 个脚本（B01 前为 70——新测试已自动纳入，与「后端 70/70」基线递增一致）；test_ephemeral_privacy / test_state_interaction 实跑通过（B01 未破坏临时轮与状态交互语义）。
4. **schema_backup.py 与 pinned 无交集**：该模块只做 schema 升级快照，不含 long_memory 数据逻辑（P0-02 时再详勘）。

至此自查饱和：又实跑两条相邻回归＋全库枚举 pinned 写入方/清理入口，无新增开放问题；开放项仍为 §G.1.2（keep 语义裁决）与开关口径（§D6）两项待拍板。

## H. 剩余未勘察区（终版）

- frontend 各组件内部状态流转（无前端切片前不需要）
- Electron 打包/部署链路细节（build_deploy.ps1 在 scripts/，部署期才需要）
- Chroma collection 跨进程并发（vector_store 的 _CHROMA_LOCK 已提供进程内串行；跨进程仅备份/恢复与未来多 worker 场景涉及）

## F. 增补篇：深度勘察（2026-09-07 续）

### F.1 core 模块全景（61 文件，17,550 行）

管道族：pipeline（1716）/llm/tool_loop/agent 之外，行为面模块齐全：activities/colists/cowriting/goals/focus/future_letters/dual_perspectives/possibilities/sealing/narrative_planner/pending_thoughts/surprise/stickers/unlock/her_profile/seasons/date_memory/relationship_(events/snapshots/versions/export)/memory_correction/explainability/privacy/proactive_policy/proactive_media/imagegen/vision/dashboard/tasks/profile/topic_memory/triple_memory/context/current_user/intent/search/knowledge/fact_decay/fact_lifecycle/features/kv_registry/**reset**。技术指导点名的目标模块全部有真实对应物。

### F.2 记忆引擎（memory/engine.py:on_message:77）

pipeline 落库后由 on_message 补充：同步入口、内部异步派发不阻塞回复——P2-02 偏好提取接入时沿此模式（不得在回复路径同步等 LLM）。

### F.3 状态机与感知（P1-03 接入面）

- state.py：AgentState 类＋`stage_of(affection)`（:46）＋rest/情绪残留/张力修复（repair_tension）/情绪归档（record_emotion_archive:627）/事件记忆（record_event_memory:755）——**情绪状态 kv 存取已有一套**（_load/_save_emotion_memory、_memory_key），P1-03 的 `state:emotions` 可复用该 kv 通道而非新发明
- mood.py：weather_baseline/_drift/mood_delta_from_text/idle_decay/today_weather（搜索＋wttr 双路，带缓存）——P1-03 消退函数与天气基线并存，天然是「旧 mood 数值继续可用」的注脚
- perception.py：perceive(text)→结构化感知（含 _debias_negatives 负向去偏、_fallback_rule 规则兜底）——关系事件夜间提取（14.10）可复用其输入与 JSON 解析骨架

### F.4 任务认领先例（P1-04 直接复用）

`backend/agent/session.py:_claim_running:196`——单条 `UPDATE ... WHERE id=? AND status='planned'` ＋ rowcount==1 判定，防并发双执行的原子 CAS。**job_runs 的租约获取应照此模式实现**（JOB-1 的 lease CAS 有仓库内先例，不是全新发明）；agent_tasks 生命周期 planned/running(/cancel_task/clear_all) 亦可供 job_runs 状态机参照。

### F.5 用量记账（P0-04 费用对账基础）

llm.py `_record_usage(channel, model, usage, prompt/completion)`（:115）→ userdb.log_usage（:1741）→ usage_log 表（user_id/channel/model/prompt_tokens/completion_tokens/estimated/ts）。通道现有 chat/perception/tool。P0-04 路由的 `route_task` 归属可直接扩 channel 枚举；价格映射 config 已有（LLM_PRICE_*_PER_MTOK）。

### F.6 搜索与工具（P3-02 现状）

- search.py：`web_search(query,max_results)`（:49），模块级 last_error，内存缓存 _cache_get/_cache_put——无 TTL 字段（24h TTL 与分时效 TTL 为 P3-02B 增量）
- knowledge.py：detect_format/parse_document/chunk_text(600,120)/ingest_document/recall_knowledge/delete_document——L01 摄入链的既有骨架，document_segments 为增量
- intent.py：classify(text)→dict——F06 意图预填的现成入口

### F.7 插件加载器细节

loader.py：_PLUGIN_STATE 状态表＋mtime 失效＋_load_disabled 持久化禁用集＋热更新 _scan_watch（后台监听 /plugins）＋register(ctx) 新旧签名兼容（_register_takes_arg）＋卸载清理 _cleanup_context。P0-01A 的插件 apply_reply 改写后置检查与此无冲突；L08「工具入口搬移」只动前端，不动此层。

### F.8 前端与 Electron

- frontend/src：App.vue＋17 组件＋api/＋utils/（chat SSE 客户端、tts.ts、portrait.ts、markdown.ts、focusMode.ts、images.ts）——P2-05 分段协议前端落点=utils/chat 相关＋MessageBubble；L08 的 VisualState 落点=新 state/ 目录
- frontend/electron：main.ts＋preload.ts（无 petWindow/stt，L09/L10 从零建，符合预期）

### F.9 切片级现状补录（第三批勘察）

**F04 专注收尾**（core/focus.py）：start/pause/resume/complete_focus(:195)/`wrapup_eligible`(:166)/`_record_focus_finished_locked` 全部存在——文档「已有入口」属实，F04 只加 build_wrapup_material＋wrapup_outbox＋JOB-1 认领。

**F02 素材互通**（core/surprise.py）：`_pick_material`(:41)/`maybe_orchestrate_surprise`(:70, 带 roll 注入可测)；P1-04 tick 的生活模板（L06）选材逻辑可参照其「概率注入」测试模式。

**F03 心事门控**（core/pending_thoughts.py）：`sync_pending_thoughts`(:59)/`due_thoughts`(:121)/`next_thought_for_stage`(:135)/`record_attempt`/`mark_expressed`/`dismiss_thought`(:166)/`forget_thoughts_for_source`(:198, 源删除联动✅)。F03 的 context_candidates 新增函数落点明确；「只有最终回复采用才 mark_expressed」语义与现有 record_attempt/mark_expressed 分离调用即可。

**P2-04 链式事件**（core/narrative_planner.py）：`plan_next(user_id, stage)`(:18)/`build_express_prompt`(:23)——现引擎是「按阶段挑一条心事」的最简规划器；链式引擎在其上扩展而非替换（保持 plan_next 语义为单步退化情况）。

**P2-03/P2-04 事件写入**（core/relationship_events.py）：`record`(:50, 幂等 INSERT OR IGNORE)/`active_events`/`invalidate_for_source`(:154)/`mark_corrected`(:169)/`record_promise_completed`(:185)/`refresh_important_date`(:200)/`event_recall`(:247)。P2-01 的 `apply_relationship_event` 应建在此模块（reducer 唯一入口原则——「同一事件由唯一 relationship reducer 同时算 global/domain 增量」L05 亦依赖此约束）。

**主动额度**（core/proactive_policy.py）：kv 键 done/claim/failure（:15-23）＋`active_count_today`/`active_done_today`/`mark_active_done`/`try_claim_active`(:84)——P2-04「先共享额度认领再表达」与 F01「显式进入页面问候不重复计后台额度」的判断函数齐备；链式可见投递日上限 1 的实现可直接挂在 try_claim_active 调用序列里。

**临时轮判定**（core/privacy.py）：`is_ephemeral_request`(:18)/`ephemeral_prompt`(:28)——P0-01A 卫生检查对临时轮的处理（不留痕但也不出门）调用点即 pipeline 的 ephemeral 分支（1677 前置）。

**F05 共读**（core/activities.py）：start_reading(:150)/set_position(:260)/save_viewpoint(:313, role 参数区分双方✅)/get_viewpoints(:393)/`pause_all_active_locked`(:43, 互斥壳)。文档点名函数全部存在；`_compile_book_summary` 在 :429 之后（_draft_material_locked 已见），reading_map 新表挂 activities 生命周期。
