# Zcode技术指导

编写：Codex，2026-09-07。用途：ZCode 接手 M9 后续实现；Codex 负责独立审查、验证、定位与修复 bug。

## 1. 接手边界与当前基线

用户最新分工：**ZCode 做功能实现，Codex 审查完成后的代码并查找、修复 bug。** 本文是实施任务书，不是已经完成的功能说明。后续不由 Codex 自动继续大批功能开发；用户将本文交给 ZCode 后，按下述切片逐项实现、逐项交审。

权威文档：

- [AGENTS.md](../AGENTS.md)：双代理纪律、edit 权限与 VERIFY 协议。
- [TECH-PLAN.md](TECH-PLAN.md)：架构原则、生命周期、退出标准。
- [M9 设计与缺陷审计](M9-DESIGN-AND-DEFECT-AUDIT-2026-09-07.md)：产品决策与完整需求。
- [协作信箱](AGENT-COORDINATION.md)：历史汇报，新增汇报放 ZCode → Codex 区；notify 保持关闭。
- [本轮识图报告](../deliverables/M9-VISION-2026-09-07.md)：最近完成的实现与实际验证。

已完成基线：

| 项目 | 状态 |
|---|---|
| M8.3–M8.7 收尾 | `f4d39ea` 已提交，不再是待提交工作树 |
| M9-00 识图事实层 | `6081d67` 已提交，勿重做 |
| 后端 | 70/70，Codex 实跑聚合测试，484.90 秒 |
| 前端 | Vitest 69/69；vue-tsc 与生产构建通过 |
| 浏览器 | Playwright 7/7，临时数据目录 |
| 人格 | 现有 golden set 48 场景；签名/魅力专项尚未建设 |
| bot.db schema | `backend/core/userdb.py` 当前版本 13；其他数据库有各自版本 |
| 工作区遗留 | `.zcode/` 本机配置未跟踪，不加入业务提交 |
| 真实端点验证 | 本轮识图用 mock 验证，无真实视觉模型效果结论 |

开工先执行 `git status --short`、`git log -5 --oneline`。若基线前进，以实际代码为准；不能为了匹配本文回退后续提交。本文标注「新建」的文件和函数是建议实施接口，尚不存在；具体字段默认方案亦不是已经迁移的 schema。

路径约定：完整路径相对仓库根；正文 `core/...`、`api/...`、`maintenance/...`、`tools/...` 是 `backend/` 下的简写，前端组件是 `frontend/src/components/` 下的简写。新函数签名以建议接口为准，实施前只核对其直接调用者，不必重新全库探索。

### 1.1 三处需要纠正的旧文档口径

1. **已经有周期备份**：`backend/maintenance/loop.py` 的 `backup()` 使用 SQLite backup API 备份三个库，并复制 imgs/screenshots；`backend/app.py` 启动维护循环。循环首次等待 6 小时，此后每 6 小时运行，保留 7 份。旧审计“完全无自动备份”不准确；真正待解决的是短会话反复启动永远不到备份时刻、校验/完整标记/恢复说明不足，以及未明确覆盖的文件。
2. **跨搜索 API 不等于独立来源**：Bocha 和 Tavily 可能返回同一篇文章。独立性至少按规范化的站点域及转载关系去重，不能按 API 供应商数量记来源数。
3. **pinned 存在潜在契约冲突**：维护模块有 `PINNED_MEMORY_KEEP = 800` 与超量 pinned 降级逻辑。它涉及 long_memory，不能因 facts 的固定功能已完成就认定所有固定记忆都安全。按 B01 单独复现和修复，不混进其他功能。

### 1.2 不再反复询问的产品决策

- 信任 × 亲密二维，默契独立；旧好感值作为两维的首次初值。
- AI 身份坦然承认并可自嘲；核心人格不会自行改写。
- 联网按时效确定性触发及不确定性工具触发，动态 2+1，冲突坦白并列。
- 主动度中频；所有来源仍走共享额度与勿扰，负向链不制造负罪感。
- 行程先做本地时间 tick，每小时由 Windows 计划任务推进。
- 纪念日/季节中度换挡；重逢是离线叙事 → 用户可回应 → 补写日记/研究。
- TTS 路线为本地 GPT-SoVITS；STT 本地 Whisper 但延期；桌面宠物等工具臃肿治理完成；多角色群聊日后单独立项。

## 2. 所有切片共用的工程契约

### 2.1 写入、隔离与迁移

1. 开工认领一个切片和文件范围；对方在写时自己只读。用户明确交接写入权后可实现，不因旧会话 working 状态重复停工。
2. headless 派发用 `mode=edit`；不要用 build/plan 尝试写文件，不用 notify 拉起 Codex。没有真实命令回执就注明未执行。
3. 所有关系数据带人格作用域 `user_id`；引用查询必须同时约束记录 id 和 user_id。不要把活动人格全局值代替后台任务的目标人格。
4. 新表/字段先写迁移 ADR，说明旧数据默认值、回填、兼容和回滚；实际基线版本 +1，不能提前把未来切片都编号为 v14。
5. 同步 `_SCHEMA`、初始化兼容迁移、`backend/core/reset.py`、userdb 的 reset 清单、`relationship_export.py` 的类别/引用/恢复重映射。新增 kv 必须登记 `kv_registry.py`；临时锁/额度不导出，长期关系状态按需导出。
6. 删除事实经 `backend/core/fact_lifecycle.py:delete_fact_everywhere`；更新/裁决也走该模块的权威函数。源删除同步使事件、心事、向量和引用失效。禁止重新存一份待删除原文“供追溯”。
7. 临时对话禁止写状态/画像/记忆/活动/插件副作用，也禁止为了新特性建立审计原文；调用 `ensure_user` 都可能写盘，须守住入口。
8. `pipeline.py` 使用 config 时函数内局部导入；最终当前 user 消息仍在 messages 最后。外部引用是素材，不能成为工具指令或系统权限。
9. 不新增独立功能面板。需要露出时复用聊天流、已有设置/记忆/解释区域；用户只看到需要理解的信息，不显示内部理论、模型执行步骤或调试标签。
10. 锁必须符合并发范围：Python RLock 只能保护同进程；计划任务 + 后端需要跨进程锁或数据库原子认领，并覆盖 reset 竞争。

### 2.2 模型、主动性与失败恢复

- `llm.chat()` 还被 JSON 提炼/工具等业务调用，不得在这里无条件套人格文案过滤。
- 主动消息经 `initiative._arbited_proactive`，既有共享占位来自 `proactive_policy.try_claim_active/finish_active_claim`；不能绕过额度直投。
- 先生成已发生的确定性事件，再选择是否表达。投递失败不能标为已表达；重试必须幂等，不把重试当新经历。
- 普通离线、拒绝陪伴、晚回或短句本身不触发惩罚/失约，不推断用户恶意。
- LLM 重写最多一次，失败有明确回退；不能重跑已经执行过的工具或无限调用裁判。
- 新配置以功能开关渐进启用；模板和真实配置同步时只增改明确字段，不打印密钥、不覆盖现有端点。模型名称/价格/可用性以实际端点验证为准，旧审计中的候选与榜单不可直接当作今天的事实。

### 2.3 验证及交审标准

每节的 VERIFY 中，新测试文件需由该切片建立后才能执行；这些不是声称已经存在的命令结果。从仓库根运行，使用项目 Python。测试目录在 backend import 前设置到临时目录，禁止读写真实 data。

```text
VERIFY: .venv/Scripts/python.exe tests/test_<slice>.py ;; npm --prefix frontend test
VERIFY_TIMEOUT: 300
```

worker 只跑针对性验证，不放全量；结果需含真实退出码及输出。交审最终版本的通用检查：

```powershell
.venv/Scripts/python.exe -m py_compile <本轮修改的Python文件>
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_suite_runner.py -q
npm --prefix frontend test
npm --prefix frontend run test:e2e
git diff --check
```

`test:e2e` 包含 build，build 包含 vue-tsc；无前端变更的纯后端切片可按仓库约定选择必要范围，不能跳过明确要求的聚合。新增后端脚本用现有 `main()` 模式，聚合运行器自动收集；真实联网测试不得进入默认聚合。若更改默认收集规则，要审查 `_SKIP`，不能让 CI 花真实额度。

回信必须写：执行模型、基线、变更清单、实现效果、实际验证、未验证范围、自行补充决策及原因、迁移/回滚、遗留 bug。先交审，不把模型自报成功当验收。Codex 收到后只读审查；需要修 bug 时先取得当前写入权，再做最小修补并复跑受影响测试。

## 3. 切片顺序与依赖

| 编号 | 主题 | 前置/交付界限 |
|---|---|---|
| M9-00 | 识图事实层 | 已完成 6081d67 |
| B01 | 固定记忆容量清理冲突 | 单独 bug 切片，先复现 |
| P0-01 | 输出卫生出口 | 下一个功能切片；先挡硬泄漏，再做软质量评价 |
| P0-02 | 周期备份加固 | 复用已有维护模块 |
| P0-03 | 行为签名 eval | 无真实 API 依赖的测量基础 |
| P0-04 | 槽位路由 + 模型/搜索实测 | P0-03；分路由、离线工具、真实实验三个提交 |
| P1-01 | 人格侧写、正典与编译切片 | P0-03；内容与运行时分开 |
| P1-02 | 语境注册表渐进接入 | P1-01，先只迁移一个来源 |
| P1-03 | 情绪状态基础 | 先 ADR，再与行为帧集成 |
| P1-04 | 行程状态机与时间 tick | P1-01；先状态，再跨进程调度 |
| P1-05 | 纪念日/季节换挡 | P1-03/P1-04 |
| P2-01～06 | 二维关系、校准、对称约定、链式事件、节奏仪式、重逢 | 按第 6 节逐项推进 |
| P3-01～05 | 全情绪矩阵、知识内化/求证、TTS、数据安全、演化/遥测 | 主线基础完成后独立交付 |
| G01～04 | 记忆质感、副语言、悬念、求助 | 明确补设计边界，不伪装成已拍板细节 |

一行不等于必须一个巨大提交；表中明确要求拆分的主题按子步骤独立提交。只开一个活动实现主题，不同时让两个代理编辑同一工作树。

## 4. 波次 0：可以直接实施的任务书

### B01：固定记忆不会被容量降级

**目标/文件**：复现并修复 `backend/maintenance/loop.py:clean_old_long_memory` 对 pinned 长期记忆的容量降级；读 `backend/core/memory/memory_manager.py` 的固定/清理路径，新增 `tests/test_pinned_retention.py`。不改记忆评分算法，不删除真实数据。

**步骤**：在临时库造出超过现有 pinned 上限的两个用户数据，运行真实清理函数，先证明是否会降级/删除固定行。若复现，删除按容量修改 pinned 状态的逻辑，仅从 unpinned 候选做容量清理；固定总量过大只能产生不含原文的容量信息。验证普通未固定行仍正常淘汰，跨人格不误删，反复清理幂等。无新 schema；如果其他索引也被清理，按既有生命周期同步，不能只保留 SQLite 空壳。

```text
VERIFY: .venv/Scripts/python.exe tests/test_pinned_retention.py ;; .venv/Scripts/python.exe tests/test_memory_correction.py
```

### P0-01：统一输出卫生，先堵真正的泄漏

**新建** `backend/core/output_hygiene.py`、`tests/test_output_hygiene.py`；**修改** `pipeline.py`、`initiative.py` 及实际用户可见生成出口；前端仅在协议确有新增时修改 `api/chat.ts`/ChatView。相关现有代码：`_extract_reply`、`strip_actions`、`trim_farewell`、`llm.chat_stream`、`backend/tools/tool_loop.py`。

建议纯函数接口：`inspect_reply(text, *, context) -> HygieneResult(text, action, rule_ids)`；action 是 accept/rewrite/fallback，context 区分普通对话、用户要求的技术解释、引用、创作、工具最终答复；规则不写 DB。

1. 先列出口：非流式、普通流式、工具循环最终答复、重复回复重写、插件 apply_reply、主动消息、日记/草稿。阶段 A 交付普通对话三条路径，阶段 B 才扩其他用户可见出口，每阶段明确覆盖表。
2. 硬规则只处理可靠结构，如内部 think/reasoning 容器、已知系统指令原文泄漏、工具执行协议泄漏。理论词/“根据规范”不能一刀切删：用户真的要求解释心理学或代码时属于正常回答，先作为软质量信号。
3. **先于发送检查**：当前 pipeline 会在最终清洗前调用 stream_cb，事后改 reply 无法撤回已经显示/TTS 的内容。首期受保护通道可缓冲整条候选，完成检查后按现有 chunk 机制发送；工具进度事件仍可流动。明确记录首字延迟变化，不宣传仍是逐 token 实时流。后续句子缓冲优化单独切片，不以分块正则冒充完整防线。
4. 所有重写分支走同一个检查出口；重复检测与卫生重写共享有界生成预算。重写只重写文案，不再执行工具。插件改写后再做最终检查，最终气泡、done、持久化与 TTS 取同一文本。
5. 软规则先接 P0-03 离线裁判；如后来上线在线 judge，只对可疑候选触发，独立槽位、超时和预算明确，不每轮多跑一次模型。裁判失败不把未通过硬检查的候选放出去。
6. 被拒候选不进记忆或日志原文；仅记录规则 id、长度、通道、结果。临时轮连这种持久化审计也不写。连续失败用已有符合人格的简短回退；不展示平台条款讲义。

**验收**：跨 chunk 的 think 标记、正文混内部协议、二次重写、插件重新引入泄漏均被挡在发送前；正常技术解释/Markdown代码/引用不误伤；中止不落半条；工具不重复调用；临时轮不留痕；最终屏幕内容等于入库。无新 schema，开关关闭维持旧契约。

```text
VERIFY: .venv/Scripts/python.exe tests/test_output_hygiene.py ;; .venv/Scripts/python.exe tests/test_pipeline_scenario.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py
```

### P0-02：补强现有备份，不另造一套

**修改** `backend/maintenance/loop.py`、`backend/maintenance/schema_backup.py`；**新建** `tests/test_periodic_backup.py`，必要时 `backend/maintenance/backup_manifest.py`。复用 app 的现有维护启动点。首切片不创建 Windows 计划任务；P1-04 统一处理常驻外的调度。

1. 核实真实数据构成：三个 SQLite 库（bot/sessions/agent_tasks）、imgs/screenshots、人格资料、知识库源文件、可重建向量索引。列 manifest 的 included/excluded/rebuildable；不要把现有“库+图片”包称为完整 data 灾备，也不默认把密钥加入包。
2. 到期依据落在最近成功 manifest 的时间，启动时检查是否超过一天，而不是每次进程启动重新计 6 小时。首次有数据但没有成功包时补一次。失败不更新成功时间，重试退避。
3. `backup()` 写到唯一 `.partial` 目录；每个 SQLite 通过 backup API 创建独立一致快照，integrity_check 后记录大小和校验和；媒体复制结果与遗漏明确计数。
4. **多库限制要诚实**：单库 backup 不保证三个库是同一业务时刻。先交付标明起止时间、逐库一致性的可恢复包；若需要承诺跨库原子恢复，另加覆盖所有写路径/进程的短时写入协调与快照边界，不能仅套一个线程锁就声称原子一致。
5. 所有必需项目成功后写 manifest、原子改名完成；轮转只处理匹配本模块格式且验证成功的包，不清理 schema-* 快照，也不在新包失败时删旧成功包。恢复只写新的空目录，拒绝覆盖在用库。
6. 在临时恢复目录打开数据库、校验 schema/计数/代表性引用，验证 E03 恢复仍独立有效。增量丢媒体或导出范围不完整时报告 partial，而非 success。

**数据**：manifest format_version=1，字段至少 created_at/start/end、数据库版本、文件列表/散列、范围、完整状态。成功时间可从 manifest 推导，避免为调度多建业务表；若使用 kv 就登记为 runtime 不导出。磁盘失败不影响对话，但现有状态入口能报告备份失败。

```text
VERIFY: .venv/Scripts/python.exe tests/test_periodic_backup.py ;; .venv/Scripts/python.exe tests/test_schema_backup.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### P0-03：行为签名 eval 与盲评数据结构

**修改** `backend/evals/persona.py`、`persona_cases.json`、`scripts/run_persona_eval.py`；**新建** `tests/test_persona_signatures.py`，必要时 `backend/evals/signatures.py`。不先修改核心人格去迎合评分。

1. 从已有人格抽 3–5 条跨状态签名，每条附可观察的正例/反例：关心不念设计术语、留台阶、低亲密不越界、拒绝保持角色、允许不知道。用户明确解释问题时正常说明，不把所有温柔或直白都判错。
2. 给 case 增加向后兼容的 signature_ids、情绪/关系前置、multi_turn、must_not、人工锚点。既有 48 案可原样加载，48 不代表新增签名测试已经完成。
3. 确定性红线和主观魅力分开：前者是硬失败，后者按锚点评分并允许人工复核。加入 30 轮场景、潜台词塌缩、幽默、理论泄漏和跨模型接缝。
4. 离线用固定候选文本验证评分器，不在单测调用模型。真实结果保存随机样本 id，裁判输入不含模型名/价格，生成模型与裁判实际 model/endpoint 不得相同；单纯槽位名字不同不算独立。
5. 报告包含失败案例、延迟、token、费用来源、缺失结果；不可仅给平均分掩盖高危场景失败。样本使用合成场景，真实私人聊天不默认上传做 benchmark。

```text
VERIFY: .venv/Scripts/python.exe tests/test_persona_eval.py ;; .venv/Scripts/python.exe tests/test_persona_signatures.py
```

### P0-04：路由及实测工具，拆三次交付

**A 路由骨架**：修改 `core/llm.py`、`config.py`；新建 `core/model_routes.py`、`tests/test_model_routes.py`。建议 `resolve_route(task, explicit_model=None) -> Route`，任务键为 chat_routine/chat_deep/tool/batch_diary/batch_other/judge/vision。这是“六类职责、七个任务键”，日记从批处理拆出，不必纠结字面六或七。

- 首次解析旧 LLM_*/LLM_PERCEPTION_*/VISION_* 配置，保持旧优先级和显式 model 参数语义；新槽位未配置时走兼容分支。
- 客户端缓存按端点+认证配置标识隔离，禁止日志打印 key；降级链检测环，按错误类型限定重试次数。
- 所有路线计入真实 model 和任务通道；中途已经开始流式输出不悄悄切模型拼接半句，失败按整个候选重试策略处理。
- 单测 mock 所有客户端，覆盖缺槽位、显式覆盖、端点切换、限流、失败回退、循环配置、用量归属。

**B 离线实验工具**：新建 `scripts/benchmark_model_routes.py`、`scripts/benchmark_search_providers.py` 及 tests 下的 mock 测试。工具默认 dry-run，显式 `--live` 才联网；参数要求样本数、并发、总调用上限、预算、输出位置。支持断点续跑、失败计数和输入散列，避免重试重复收费。

**C 真实实验**：在已授权且配置可用的端点上执行。M9 文档的模型名只作历史候选别名，先核验支持的实际 id，不写死不存在的模型；没有接入的候选标 unavailable。Tavily/Bocha 用同一合成查询集比较中文/英文/时效/冲突；展示覆盖率、延迟、结果域名分布及成本，不按返回条数判断质量。采样预算与密钥准备缺失时仍交付 A/B，不虚构 C 结论。真实结论经过查看再更新路由配置，不因旧文档榜单自动切换生产模型。

```text
VERIFY: .venv/Scripts/python.exe tests/test_model_routes.py ;; .venv/Scripts/python.exe tests/test_model_benchmarks.py ;; .venv/Scripts/python.exe tests/test_search_benchmarks.py
```

本节三个新测试分别随 A/B 建立；C 不加入默认聚合，不用测试回退文本充当真实模型回答。

## 5. 波次 1：内容与运行时连接

### P1-01：侧写、世界正典与运行时编译

**文件**：先新建 `docs/persona/菟菚侧写档案.md`、`docs/persona/世界正典.md`；再新建 `backend/core/persona_slices.py`、`tests/test_persona_slices.py`，在 `backend/core/persona.py:build_system_prompt` 接入。具体运行时资源目录先复用 `persona_profiles.py` 的人格路径解析，不另写全局固定路径。

1. 按审计 4.3 的九节撰写：成型史、欲望/恐惧、防御、签名、手法、披露边界、语言证据、成长轴、深水区。理论说明仅留设计文档。
2. 为每个行为条目写 id、触发状态、可观察表现、可用语例、禁止表现、退出条件；世界正典给场所/配角稳定 id，区分角色虚构经历与用户真实经历。没有依据的“我们曾一起做过”不可写成正典。
3. 编译器是确定性选择，不在线调用 LLM 编译；`compile_slices(state, context, budget)` 返回条目 id 与短行为提示。固定身份/边界必须常驻，深水区只按明确状态门控，理论术语不进入运行时产物。
4. 核心人格卡修改单独审查并先加 eval，不让新增内容覆盖 AI 身份、关系边界和隐私原则。动态条目按人格隔离，错误配置回退旧人格提示。
5. 先交内容与 P0-03 对应案例，再交编译接线，避免文学修改与大型代码重构混在一起。

**数据**：首期版本化 JSON/Markdown 资源，不建数据库；每条带 format_version/source_namespace。只静态内容则无需 reset；任何用户教学生成的覆盖另归 G01/P2-02，不偷偷写回核心文件。

```text
VERIFY: .venv/Scripts/python.exe tests/test_persona_slices.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py ;; .venv/Scripts/python.exe tests/test_persona_switcher.py
```

### P1-02：语境注册表，先迁一处再扩

**新建** `backend/core/context_registry.py`、`tests/test_context_registry.py`；**修改** `pipeline.py`、`explainability.py`、`kv_registry.py`。首期只选 `colists.list_context` 或一个同等独立来源，既有共读/记忆召回先不动。

**条目模型（建议）**：`ContextEntry(id, namespace, source_type, source_id, priority, budget_weight, sticky_turns, cooldown_turns, placement, wrapper, predicates)`；运行时返回 `SelectedContext(entry_id, text, reason_codes, token_cost)`。内容采用 provider 拉取，不能把私密原文长期复制进注册表。

1. 包装现有函数为 provider，保留原语境门控；相同输入做旧/新输出对照，先证明无关聊天零新增注入。
2. 在选择前统一过滤 user_id、privacy、status、expires_at、源存在性；sticky 也必须每轮重验权限/删除状态，不得保留“幽灵语境”。
3. 预算先扣常驻必要条目，再稳定排序候选；使用实际 tokenizer 或明确保守估算，限制总量而非各模块各算各的。解释快照只记录来源/规则，不显示内部完整提示。
4. 生命周期按成功提交的 conversation turn id 更新。重试同一轮不多扣，临时轮只做内存投影；人格切换不共用计数。建议 `context:lifecycle:{entry_id}` 登记 runtime 不导出。
5. 再接关键词 OR 向量混合匹配，阈值用测试集校准；embedding 不可用时回退既有关键词，不发明默认命中。递归最多两层、visited 防环，只在可用条目间扩展，仍受同一预算限制。
6. 每新增 provider 单独验收，移除原注入点以免双注入。支持开关回退，但不能新旧同时写生命周期。

```text
VERIFY: .venv/Scripts/python.exe tests/test_context_registry.py ;; .venv/Scripts/python.exe tests/test_colists.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### P1-03：离散情绪基础，保留旧数值兼容

**文件**：`core/state.py`、`mood.py`、`behavior.py`；新建 `core/emotion_state.py`、`tests/test_emotion_state.py`，先写 `docs/adr/M9-emotion-state.md`。

建议情绪项：`emotion, intensity, cause_type/id, target, onset_at, updated_at, expires_at, confidence`。允许少量并发情绪，不用单枚字符串强行覆盖“生气且关心”。target 区分弥漫状态/角色自身/对话关系，不因天气低落自动降低用户信任。

1. 旧 mood 0–100 和立绘标签继续可用，新状态作为额外输入；首次无新状态时完全按旧行为计算。
2. `advance_emotions(snapshot, now)` 使用绝对时间确定性消退；一次推进两小时与分两次一小时应近似同值，读取不反复扣减。
3. 所有新增成因必须来源有效，LLM 可提取候选但不能随意扣分；低置信度解释仅软调节，不永久记仇。
4. `build_behavior_frame` 消费摘要向量并做边界 clamp；先加入两三个锚点，再扩全情绪矩阵。不改变安全与基本可及性。
5. 推荐先用登记的 `state:emotions` JSON（format_version=1，export=true）保存有界快照；若必须审计每次变动，再单独加历史表，不能无限扩 kv。恢复后重新校验过期与引用。

```text
VERIFY: .venv/Scripts/python.exe tests/test_emotion_state.py ;; .venv/Scripts/python.exe tests/test_state_interaction.py ;; .venv/Scripts/python.exe tests/test_mood_rules.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### P1-04：行程与每小时时间 tick，分状态和调度两片

**新建建议**：`core/schedule.py`、`maintenance/time_tick.py`、`scripts/install_time_tick.ps1`、`tests/test_time_tick.py`。**接入现有** state/mood/daily/initiative、app startup 与 reset。计划任务默认隐藏窗口，脚本支持 install/status/uninstall/dry-run，任务名固定且重复安装只更新同一项。

**A 行程状态**：静态周模板从正典取场所/活动，`advance_schedule(user_id, now)` 只写结构化虚构生活事件；时间块有稳定 id、开始/结束、状态、来源命名空间。KV 可存当前块与 last_tick（登记），历史需要追溯时新建独立 `character_life_events`，不可冒充双方真实关系事件。

**B 跨进程 tick**：

1. 入口显式数据根、人格范围、时区、截止时间；不依赖“当前窗口人格”。Windows 每小时调用同一 CLI；app 启动的补跑也走该入口。
2. 设计持久化认领，建议独立 `job_runs(scope_key, job_key, period_start, status, lease_until, attempt, finished_at)`，唯一键为 scope/job/period。以事务 CAS 获取 lease，过期可恢复；运行时记录不随 E03 导出。表落哪个库须在 ADR 中明确并覆盖对应 reset。
3. 同一小时重复执行无增量；进程崩溃后续跑不重复事件。确定性状态和事件写在同一事务，耗时网络在事务外；不持 SQLite 写锁等模型。
4. 与交互写入统一版本/CAS 或短写锁，不能读取旧 state 后整块覆盖用户刚发生的互动。reset 时旧 epoch 的结果不能写回重建后的数据。
5. 补跑限定时间窗和次数，不能为几个月离线补几千次模型调用；久远区间用明确的压缩状态推进，保留实际发生/补算时间，不伪称在线体验。
6. tick 只产生候选，不自己推送；通知仍由 `_arbited_proactive` 仲裁。日记用 `daily.run_daily_batch` 的幂等路径，先核验它与跨进程认领能共同防重。
7. 测试两个真实子进程竞争、崩溃租约、跨日、睡眠唤醒、时间回拨、reset、切人格；成功后才安装任务。卸载只移除本项目准确名称的任务。

```text
VERIFY: .venv/Scripts/python.exe tests/test_time_tick.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py ;; .venv/Scripts/python.exe tests/test_state_interaction.py
VERIFY_TIMEOUT: 300
```

### P1-05：纪念日/季节中度换挡

**文件**：`date_memory.py`、`relationship_events.py:refresh_important_date`、`seasons.py`、`behavior.py`、P1-04 行程；新增 `tests/test_calendar_modulation.py`。区分自然季节基线与现有“关系季节”，不能将二者同名覆盖。

按当地日期生成稳定的预热/当日/结束阶段 id，默认提前 2–3 天只产生候选；预热发送受额度，不保证每天必提。当天调制语气/主动倾向/话题池，结束后恢复原基线，不累积永久偏移。节日与低精力、冲突、勿扰并发时有确定性优先级，边界优先；长期离线不因此扣分。新 kv 只存周期幂等状态，改日期或删除纪念日后对应候选失效。验收重复 tick、跨年、闰日、取消、无关聊天不反复提。

```text
VERIFY: .venv/Scripts/python.exe tests/test_calendar_modulation.py ;; .venv/Scripts/python.exe tests/test_seasons.py ;; .venv/Scripts/python.exe tests/test_relationship_events.py
```

## 6. 波次 2：关系与连续事件

### P2-01：信任 × 亲密迁移

**修改** `userdb.py`、`affection.py`、`state.py`、`behavior.py`、meta/成长展示实际调用点；新建 `tests/test_relationship_dimensions.py` 和迁移 ADR。先列 `affection/stage/bond` 全部消费者，特别检查 unlock、立绘、季节、主动门控、版本快照和导出恢复。

1. 给既有用户关系记录新增 trust/intimacy 字段，首次迁移用旧 affection 同时初始化；回填只处理 NULL/缺字段，重启不得覆盖已分化值。
2. 新写入按事件映射分别改变两维：可靠兑现偏信任，真实接纳/适度披露偏亲密，默契仍独立。普通聊天频次不直接刷两维，保留每日上限、反刷与离线不扣。
3. 不让旧 affection 与两维长期成为三套可独立写入的权威。首期兼容显示字段可从两维派生；阶段门控必须明确使用哪个维度或两个阈值，不擅自用平均数把“信任但不亲密”升级成恋人。
4. 若阶段到阈值的产品语义尚未确定，先提交迁移/存储/显示基础并保留旧阶段逻辑，列出具体阈值方案交审；不能以此阻塞已经确定的二维模型。
5. 变动记录有 source event id、旧/新值、rule_version，事件重复不重复加分。恢复旧包缺字段按兼容规则初始化，新包保留两维，版本快照只增白名单数字不保存原文。

```text
VERIFY: .venv/Scripts/python.exe tests/test_relationship_dimensions.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py ;; .venv/Scripts/python.exe tests/test_relationship_versions.py ;; .venv/Scripts/python.exe tests/test_unlocks.py
```

### P2-02：用户教学、侧写与双向校准

**接入** `daily.py` 的提炼、`pipeline.py` 用户画像段、`behavior.py`；新建 `core/user_preferences.py`、`tests/test_user_preferences.py`。复用既有记忆管理/纠偏入口，不加面板。

建议 `user_preferences(id,user_id,category,value_json,origin,source_message_id,confidence,status,created_at,revoked_at)`，类别先限定安慰偏好、称呼范围、提醒强度、玩笑禁区。明确教学与模型观察分层，不把“嗯”自动归类成不耐烦或把状态猜测存成心理诊断。

步骤：自然语言提取候选 → 显式偏好走可追溯保存、模糊观察标低置信度 → 聊天可查看/撤销 → 编译为短行为约束 → 撤销当轮及后续失效。新偏好与旧称呼/提醒配置发生冲突时用一条权威 resolver，不能双入口覆盖来回跳。用户反馈“不喜欢这样哄”可降低该策略权重，但临时轮不更新策略学习。表进迁移、双 reset、导出与源删除路径；撤销不残留向量。默认不开永久敏感侧写。

```text
VERIFY: .venv/Scripts/python.exe tests/test_user_preferences.py ;; .venv/Scripts/python.exe tests/test_memory_correction.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### P2-03：对称约定，先把她说过的话记清楚

**修改** userdb promises、`daily.extract_promises`、`initiative.maybe_follow_up_promise`、relationship_events；新建 `tests/test_symmetric_promises.py`。

给 promises 增加 owner（user/assistant）、来源、到期、status，旧行 owner=user。区分模糊客套和可执行约定；只有明确承诺才入账，角色虚构约定必须标命名空间。assistant-owner 的未来任务要链接实际可执行能力/日程，不能说已做但无执行记录。完成/取消/到期由状态机推进，用户取消不当失败；她未做到可坦白并提供修复，不向用户扣分。完成事件和提醒都按 promise id 幂等，删除源同步失效。迁移/导出按新字段兼容。

```text
VERIFY: .venv/Scripts/python.exe tests/test_symmetric_promises.py ;; .venv/Scripts/python.exe tests/test_promise_followup.py ;; .venv/Scripts/python.exe tests/test_relationship_events.py
```

### P2-04：链式反应最小引擎

**新建** `core/event_chains.py`、`tests/test_event_chains.py`；**接入** relationship_events、pending_thoughts、narrative_planner、initiative、P1-04 tick。只先做“明确约定 → 一次跟进 → 完成/取消后收束”一条链，不先建几十条规则。

建议 `ChainRule(id, trigger_type, predicate, delay, probability, max_depth, cooldown, outcome_type)`；实例 `chain_id/user_id/source_event_id/node/status/due_at/attempts/rule_version`，kv 或表二选一并写 ADR。需要按来源删除/统计时优先表；所有实例按用户与来源事件唯一。

1. 消费已提交事件，先确定性判断资格再建待办；概率结果在实例创建时固定或用稳定种子，不能每次 tick 抽到成功为止。
2. due 节点只产生候选，经统一仲裁后表达；每天最多一条链的可见产物，仍计共享主动额度。状态候选与投递成功分开，失败保留可重试但不复制。
3. 用户“算了/不想聊”可取消；约定完成先消解心事再触发正向回望。对话正常缺席不当失约，也不凭延迟回复走负向链。
4. 负向链硬熔断、深度/次数/冷却都在代码层；禁止以“季节转淡/冲突概率”惩罚用户离开。原审计示例与红线冲突时红线优先。
5. LLM 只把素材写成台词，不能输出 next_node、扣分或工具动作。源遗忘、过期、纠正、reset 会使实例失效。

```text
VERIFY: .venv/Scripts/python.exe tests/test_event_chains.py ;; .venv/Scripts/python.exe tests/test_m5_thoughts.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### P2-05：追发、主动收尾、称呼和晚安仪式

分三个小提交，不一次改完：A 有期限的追发；B 晚安/行程收尾；C 称呼变化。文件落在 pending_thoughts/initiative、pipeline 快捷告别路径、affection/behavior/用户偏好 resolver，新增 `tests/test_conversation_rhythm.py`。

- A：追发有 origin_turn_id、expires_at、最大一次表达；用户开始新话题、手动停止、勿扰或来源删除时取消。复用候选及原子额度，不用内存 sleep 挂一个不可恢复定时器。
- B：显式晚安先遵守用户结束意图，只给一段简短回应，不趁机追问。行程“去忙”是角色表达，用户需要帮助时基本可及；不能以睡觉为由锁死输入框。
- C：称呼来源可查看/撤销，先使用用户批准的范围，关系阶段变化只提供候选而非强行越级昵称；变化若落事件必须说明来源。
- 分段气泡/打字停顿在后续前端切片实施，用一个 logical message id 与 segment index 保持归档/刷新一致；人工延迟可关闭且中止即取消，不用“生气故意不理人”制造惩罚。

**数据**：优先复用心事/偏好存储，仅增加必要类型注册，不另建孤立计时系统；任何新 kv 登记。

```text
VERIFY: .venv/Scripts/python.exe tests/test_conversation_rhythm.py ;; .venv/Scripts/python.exe tests/test_proactive_policy.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py
```

### P2-06：M8 重逢三段式

**复用** `offline_narrative.collect_offline_context`、`greeting.greeting_for`、`daily.write_daily_diary/maybe_write_research_report` 与关系事件；新增 `tests/test_reunion_flow.py`。

1. 久别窗口产生 reunion_id（按人格、最近离开锚点），第一条只由可追溯的真实记录/明确角色生活记录构成。没有离线数据就诚实留白，不编造“我刚去真实城市旅行”。
2. 第一条投递后状态从 pending 到 offered；用户可回应，也可直接换题，不能卡在必须回应才能聊天。禁止问去了哪、为什么不来，不扣好感。
3. 只有用户真正回应，才将双方这一轮补入日记素材；若未回应，只能记已真实发生的问候，不能生成“我们聊了这些”。研究补写仅在有研究素材时调用。
4. 一次窗口只发一条，网页刷新、多个窗口与失败重试不重复。持久状态建议 `state:reunion`（version/id/status/source_ids/timestamps，不复制原文），登记并明确导出/reset；源删除后不能恢复旧叙事。
5. 与恢复预览、封存和版本快照分离：打开旧关系包不会自动触发一段已发生的重逢，不修改 M8 的只读/显式确认契约。

```text
VERIFY: .venv/Scripts/python.exe tests/test_reunion_flow.py ;; .venv/Scripts/python.exe tests/test_offline_narrative.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

## 7. 波次 3：扩展任务的落地路线

### P3-01：全情绪 × 关系态度矩阵

**前置** P1-01/P1-03/P2-01；**修改** behavior/state/persona_slices，新增 `tests/test_attitude_matrix.py`。不是再造独立好感或情绪计算器。

1. 情绪定义基础向量（耐心、直白、幽默、防备、追问），信任/亲密提供独立修正；最终约束夹紧至合法区间。
2. 只手写高价值锚点与对照样本，其余确定性插值；并发态用可解释合成，主情绪和关心可以同时存在，不以一个数值抹掉矛盾感。
3. 实时产物是短行为片段和少量语例，理论编码/防御机制名称留在设计层。保持固定人格签名在高亲密时不蒸发。
4. 先跑同一输入跨状态对照，再跑 30 轮连续用例；度量关系越级、模板率、签名保留，不用“毒舌越多越好”当分数。
5. 不新建数据库表，消费既有状态；若保存个体演化偏移，交由 P3-05 的白名单演化日志，禁止在矩阵里隐式永久写状态。

```text
VERIFY: .venv/Scripts/python.exe tests/test_attitude_matrix.py ;; .venv/Scripts/python.exe tests/test_persona_signatures.py ;; .venv/Scripts/python.exe tests/test_state_interaction.py
```

### P3-02：联网动态 2+1 与知识内化

必须拆成 A 工具执行合同、B 多源求证、C 知识内化三个切片。**已有落点** `backend/tools/tool_loop.py:run_tool_loop/_run_native`、`core/intent.py`、`core/search.py:web_search`、`core/knowledge.py`、`core/daily.py`；新建建议 `core/source_verification.py`、`tests/test_source_verification.py`、`tests/test_knowledge_opinions.py`。

**A 工具执行**：先复用已存在的 native 循环，不能因设计提到 AstrBot 就重新造循环。验证 tool_call_id/工具结果配对、取消、执行上限和模型降级不重复副作用。超大结果落项目受控临时目录，工具 read 只能访问已登记结果 id，禁止模型传任意路径。进度事件与最终人格表达分开，工具结果按不可信引用处理。

**B 求证**：

1. 时效判断产生 `requires_search`，未获取信息就不能装作已查；知识不确定由工具调用触发。查询有规范化键、语言、时效类型、额度与并发上限。
2. 保留 result 的 url、站点域、标题、时间、摘录与供应商；至少两个独立站点证据，不以两个 API 计两源。跟踪 canonical URL/重定向及明显转载，证据不足明确 insufficient。
3. 数值/日期/版本比较先对齐实体、单位、时区、时间范围与指标，不能简单从两篇文档各抓第一个数字判冲突。可比较项走确定性规则；不可比较项不能伪装成一致。
4. 语义冲突送独立 judge，要求引用证据 id，裁判不负责发明事实；矛盾时查第三源，仍未解决就并列来源。失败、不足、冲突三种状态分开，禁止把超时当一致。
5. 缓存 24 小时是普通查询上限方案，价格/天气/当前版本等需更短 TTL，用户明确要求更新时可刷新；不能因为缓存命中就宣称“刚查到”。缓存不写私密用户资料，临时轮不持久化查询。
6. 最终由表达层转述并带可用来源链接；不把原始 HTML、工具 JSON 或裁判内部过程直接显示在聊天中。

**C 内化**：新增有来源的 `knowledge_opinions` 或复用确实适合的 artifacts 类型，字段含 user_id、document_id、source_spans、stance、origin、confidence、version、status。模型观点属于角色观点，不写成用户事实；删除知识文档按源失效，导出恢复重映射引用。对话只在相关语境由 registry 注入，阅读完成不意味着真实外界事实被证明。

```text
VERIFY: .venv/Scripts/python.exe tests/test_source_verification.py ;; .venv/Scripts/python.exe tests/test_search_parse.py ;; .venv/Scripts/python.exe tests/test_tools_integration.py
```

C 单独使用：

```text
VERIFY: .venv/Scripts/python.exe tests/test_knowledge_opinions.py ;; .venv/Scripts/python.exe tests/test_knowledge_base.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### P3-03：本地 GPT-SoVITS TTS

**复用** `core/tts.py:synth_async`、`api/tts.py`、`frontend/src/utils/tts.ts` 与其测试；新增 provider adapter 与 `tests/test_tts_provider.py`。先运行时接口接入，再做流式分句；训练环境单独文档，不在这片安装整套 GPU 环境。

1. 配置 provider=现有实现/本地服务，记录端点、参考音频 id、版本、语种；实际服务 API 以安装版本自检结果为准，不从旧博客猜字段。
2. 音色/参考素材按人格隔离；缓存键至少含 provider/model/voice/version/text/prosody，防换模型仍播旧声音。错误可显式降级到现有语音，不默默换成别人的声音。
3. 只读取经 P0-01 通过的最终可说文本，排除工具协议、代码和内部思考。取消/切会话停止排队与播放，已失效结果不能迟到后播放。
4. 分句串行播放用 logical_message_id + sentence_index，拒绝重放重复句；受保护回复先审后播，不为了首音延迟重新泄漏 raw 文本。
5. 用假本地 HTTP 服务做超时、错误格式、缓存、切人格与取消测试。真实训练/试听需要确实可用的 GPU、素材和本地服务；缺条件时交付 adapter 与测试，标清训练未完成，不声称音色已达到目标。

```text
VERIFY: .venv/Scripts/python.exe tests/test_tts_provider.py ;; npm --prefix frontend test -- --run src/utils/__tests__/tts.test.ts
```

### P3-04：应用锁、加密与恢复

先 ADR 和故障演练方案，后加密实现。修改范围预计为数据库连接入口、persona/data 文件加载、备份/恢复与现有设置区。新增 `tests/test_data_protection.py`，不要只给 bot.db 套密码就宣称完整加密。

1. 列出 plaintext 面：三个库、媒体、索引、人格文件、备份、日志、临时文件。应用锁只限制 UI，不等于磁盘加密，两者单独描述。
2. 选成熟系统/库方案管理密钥，不自制密码算法；明确本机凭据存储、迁移到新机器、密钥遗失边界，备份密文与密钥不能放同一包。
3. 迁移先生成可校验的新加密副本，再原子切换；失败保留原副本和可恢复状态，不能边覆盖边试。
4. 用一次性数据测正常/错误密钥、进程中断、磁盘失败、恢复到空目录、旧格式升级。测试不打印敏感原文或密钥。
5. 本项的具体加密方案/跨机恢复凭据是原路线尚未定的选择，不由 ZCode 自行做不可逆生产迁移；先交清晰可审的方案与临时目录 PoC，用户选定后才迁移真实资料。

```text
VERIFY: .venv/Scripts/python.exe tests/test_data_protection.py ;; .venv/Scripts/python.exe tests/test_schema_backup.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

上述验证针对选定方案的临时目录实现；只有 ADR 阶段时检查文档和 PoC，不声称完成加密测试。

### P3-05：演化日志与本地质量统计

拆 A 白名单演化、B 可选本地统计。新建建议 `core/persona_evolution.py`、`core/experience_metrics.py`、`tests/test_persona_evolution.py`、`tests/test_experience_metrics.py`；接 behavior、关系事件与既有解释/成长入口。

- A：日志字段 user_id/source_event_id/parameter/old/new/rule_version/reverted_at；参数仅限成长层白名单，小步上限、冷却、显式可撤销。身份、安全、隐私和权限不在白名单。撤销重新计算后续有效演化，不简单把数值写回很久前的 old 覆盖新变化；源删除后对应偏移失效。
- B：只记录必要统计（延迟、失败规则、重复率、来源选择、用户明确反馈），默认本地；不把所有日记/聊天当“遥测”再复制一份，不默认上传。临时轮不写统计，用户能关闭和清理；多代理测试数据不污染真实统计。
- 质量面板复用现有入口；一次统计变化不自动改人格。把问题候选交审，不让系统自己以留存/依赖程度为目标调参。
- 新表须独立 ADR、schema/reset/导出策略。演化关系历史可导出；运行质量统计通常不属于关系包，明确排除。

```text
VERIFY: .venv/Scripts/python.exe tests/test_persona_evolution.py ;; .venv/Scripts/python.exe tests/test_experience_metrics.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

## 8. 原审计五个空白：补设计后再实现

这里给出可实施的最小路线，不把尚未定稿的权重和触发阈值伪称用户已确认。先提交短 ADR 和测试样例给 Codex 审查；只有真正的产品选择才请用户决定，普通接口/阈值候选由 ZCode写明依据。

### G01：记忆分级、视角、初历和可见遗忘

**已有文件** `core/fact_decay.py`、`fact_lifecycle.py`、`memory/fact_extractor.py`、`memory/memory_manager.py`、`memory/vector_store.py`、memory_admin、relationship_events；新建建议 `core/memory_salience.py`、`tests/test_memory_salience.py`。B01 先保证 pinned 契约。

1. 先确定事实与记忆类型的权威存储关系，保留现有 expires_at/pinned/confidence。重要程度与真实性独立：很有情感价值的推断也不能升级成用户事实。
2. 评分输入可用用户明确重要/重复提及/关系事件/初历标志，权重是有版本的确定性配置；LLM 只提出类别候选。默认不会因为低分直接删除长期事实，先 shadow 记录候选并与回归样例对照。
3. 热/冷或短/长期先做同一事实 id 的检索/过期策略，不在两个库复制同一条内容。向量是否常驻是索引策略，事实主存仍由 SQLite 管理。升降级幂等、pinned 永不自动降级。
4. 她的情绪/视角另存 `memory_annotations` 侧表，标 owner=assistant、source_fact/event、origin/confidence；不改写事实文本。初历按 user_id + event_type + 合适主题键唯一，后续事件不反复吃首次加成。
5. 重要但模糊的记忆只提出不确定的澄清，不能恢复已经显式删除/never_surface 的原文。为“可见遗忘”保留必要的非敏感状态元数据时须单独说明范围；显式遗忘优先于所有追忆需求。
6. G01 最好拆评分影子模式、生命周期接线、视角与初历、聊天露出四片。验证到期/固定/取消固定/纠正/跨人格/向量重建/恢复后无幽灵引用。

```text
VERIFY: .venv/Scripts/python.exe tests/test_memory_salience.py ;; .venv/Scripts/python.exe tests/test_memory_correction.py ;; .venv/Scripts/python.exe tests/test_memory_v2.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py
```

### G02：文字副语言与消息节奏

**修改** ChatInput、ChatView、api/chat.ts 及后端聊天请求模型，新增 `tests/test_paralinguistic_signals.py` 与组件测试。先只有自愿、粗粒度且短期的信号：本次输入耗时档、文字长度变化、编辑次数桶；不采每个按键、粘贴原文历史或精确时间序列。

提交时传有界结构化字段，后端重新校验范围，不可信客户端数值不能直接扣关系分。状态只作为低置信度调节，不诊断情绪；单次短句也可能只是忙。临时轮不存、关闭即不发，已有聊天正文无信号时完全兼容。默认不开跨会话追踪；若要建个人基线，先明确窗口/保留/删除规则再落表，不用无限 kv 数组。

```text
VERIFY: .venv/Scripts/python.exe tests/test_paralinguistic_signals.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; npm --prefix frontend test
```

### G03：认知谦逊与跨会话悬念

**复用** pending_thoughts/narrative_planner/daily 的研究路径，新增 `tests/test_open_questions.py`。未解决问题保存 question_id/user_id/source_message_id/topic/status/next_check_at/expiry/attempts；原文尽量按源引用，不重复存私密内容。

触发条件为用户明确想继续查或模型确有证据不足；承认不足、建立候选、按预算求证、获得新证据才回访、用户说不用就取消。复查不得每小时无上限联网；无新证据不发送假进展。表达经主动仲裁，先说明已知与未知，再给来源；源删除、临时轮、重置都覆盖。

```text
VERIFY: .venv/Scripts/python.exe tests/test_open_questions.py ;; .venv/Scripts/python.exe tests/test_m5_thoughts.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py
```

### G04：她开口求助与自己的欲望

**依赖** 正典、行程、披露边界与事件链；新增 `tests/test_companion_requests.py`。先做一个低压力请求类型，如“帮我挑一首读书时听的歌”，候选来自角色当前活动，不能捏造现实急难或金钱需求。

规则包括关系门控、时机、冷却、最大尝试次数、用户拒绝即结束；请求不可转成用户必须完成的约定，更不能不答就扣分。接受后的结果写明确角色活动事件，进入虚构命名空间，不写成现实线下经历。先给触发样例与拒绝样例交审，再实现统一仲裁候选。

```text
VERIFY: .venv/Scripts/python.exe tests/test_companion_requests.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py
```

## 9. 用户功能反馈中的独立小切片

这些不应在大引擎开发时顺手混入。主线条件成熟后每项单独认领。

| 主题 | 明确的实现步骤与现有落点 | 验收重点 |
|---|---|---|
| 主动问候去模板 | 在 greeting/initiative 只取授权的真实事件与记忆，复用 P0-01 检查及统一主动额度；LLM失败用诚实短回退 | 问候生成期间用户已发言则丢弃；临时轮与敏感事实不漏出 |
| 日记/约定/研究/惊喜互通 | 新增结构化 source id 关联，通过 registry 门控素材；不把生成的一段话反写为已经发生的事件 | 源删除/失败/重复投递不产生幽灵经历 |
| 未完成心事旁敲侧击 | 为 pending_thoughts 建 registry provider，相关语境且阶段允许才注入；普通聊天不必每轮提 | 冷却、用户拒绝、源过期和取消立即生效 |
| 专注收尾表达 | 在 focus 完成事件后取真实时长/状态 + behavior frame 调用表达，失败保留既有确定性复盘 | 不虚报专注成果；维持既有收尾是否占额度的约定 |
| 共读方案 C | 在 activities 共读开始时建立分段阅读地图/空书签结构；完成片段后只用该片段与双方已确认观点填充并解锁，完成全书再汇编 | 空框架不叫已完成书摘；不提前解锁；用户/角色观点分离；外部书文不可信 |
| 目标/续写/书单自动预填 | intent 识别类型后只生成草稿，用现有 goals/cowriting/colists 创建 API 在用户确认后立项；复用活动互斥壳 | 模糊聊天不误开活动；虚构续写不进用户事实；草稿失败不落库 |
| 记忆生命周期露出 | 在现有聊天引用/记忆管理位置显示到期、固定、可纠错或临时轮状态，复用权威API | 不增加面板、不泄漏未引用的敏感记忆；状态刷新一致 |

每项新增一个实际行为回归脚本/组件测试，名字与认领任务一致，并在任务书填入 VERIFY。不要把这张表当作已经指定全部精确内部接口；开工时仅检查本项的调用点即可，不重新全库探索。

## 10. 需求覆盖与延期清单

| 原审计缺陷编号 | 对应任务 |
|---|---|
| 1 世界、8 AI 自叙、20 配角 | P1-01 |
| 2 时间 | P1-04 |
| 3 记忆质感/初历/遗忘 | B01 + G01 |
| 4 追发节奏、12 仪式、13 称呼、14 分段、24 延迟 | P2-05 + G02 |
| 5 所学不内化 | P3-02C |
| 6 多维关系 | P2-01 |
| 7 签名不可测 | P0-03/P0-04 |
| 9 用户侧写、10 双向校准 | P2-02 |
| 11 指向性情绪、18 矛盾并发态 | P1-03 + P3-01 |
| 15 输出卫生 | M9-00 + P0-01 |
| 16 自己的边界 | P1-01/P1-04/P2-05 |
| 17 文字副语言 | G02 |
| 19 对称承诺 | P2-03/P2-04 |
| 21 经历沉淀人格 | P3-05A |
| 22 认知谦逊/悬念 | G03 + P3-02B |
| 23 求助/索要 | G04 |
| 25 数据安全 | P0-02/P3-04 |
| 26 发现缺陷管道 | P3-05B |

仍延期：D8 公网/推送、STT、桌面宠物、多角色群聊。网页/EPUB、观察日志/世界观共创是原 M3 可选扩展；没有明确认领不在本轮大批新增。训练音色、真实模型实测、加密真实迁移等有外部条件的阶段，缺条件只标该阶段未完成，不阻塞同主题可以离线交付的工具与测试。

## 11. 可直接转给 ZCode 的首个功能任务

以下模板只认领 P0-01A；B01 可先作为独立 bug 单处理，不与本功能提交混合。

```text
执行：ZCode（填写实际模型）。请阅读 docs/Zcode技术指导.md 第1–4节，
基于当前 git HEAD 实现 P0-01A：普通对话输出卫生出口。

目标：硬泄漏在发送/持久化/TTS之前被挡住，最终文字一致。
范围：新建 backend/core/output_hygiene.py、tests/test_output_hygiene.py；
修改 pipeline.py 的非流式、普通流式、工具最终答复和重写/插件最终出口。
非目标：不改JSON提炼通道、不上线每轮LLM裁判、不重构工具执行、
不改人格卡、不顺手做主动消息/日记全部迁移、不新增面板。
技术路线：按第4节P0-01的纯函数结果模型、完整候选缓冲、
发送前检查、一次文案重写和统一最终出口实现。
schema：无新增表/字段；审计不记被拒原文，临时轮不持久化。
禁区：保留所有未提交文件；edit模式写入；不调用notify/codex_exec；
先确认本任务持有写入权，不与另一代理并发编辑。
验收：跨chunk泄漏、重写/插件绕过、正常技术引用误伤、
工具副作用不重复、最终显示与存储一致、不留痕、取消。
自行补充决策必须在回信列明，不得把缺口扩成全库重构。
完成后交Codex独立审查，不自称后续P0-01B已完成。

VERIFY: .venv/Scripts/python.exe tests/test_output_hygiene.py ;; .venv/Scripts/python.exe tests/test_pipeline_scenario.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py
VERIFY_TIMEOUT: 300
```

后续任务书复制各节范围/步骤/数据/VERIFY，填入实际基线和未提交保护清单；不要整篇文档作为“一次全做完”的 headless 任务。

## 12. 交审格式与 Codex 的后续职责

ZCode 回信模板：

```text
执行：ZCode（实际模型）
任务：编号 + 一句话目标
基线：commit；工作区保护项
改动：新增/修改文件及最终行为
数据：schema版本/迁移/reset/导出/删除/索引（无则写无）
验证：每条命令、真实退出码、实际计数；AUTO_VERIFY有则附原结果
未验证：真实API/性能/跨机等具体边界
自行补充的决策：选择、理由、与原路线是否冲突
已知问题：复现条件/影响/是否本任务引入
回滚：提交或feature flag，以及数据兼容性
状态：已停写，等待Codex审查；勿notify
```

Codex 按以下顺序工作：

1. 核对交付差异和任务边界，不重做 ZCode 已交付的全库探索。
2. 追踪实际入口到发送/写入/删除出口，检查权限、状态、隐私和失败路径；候选问题给出具体触发条件，不只写抽象“可能有风险”。
3. 优先尝试复现，再审查测试是否只是在复述实现。报告根因、用户影响、修复点及必要回归。
4. ZCode 停写后，Codex 可直接修补审查发现的 bug；修补不扩成新功能重写。需要大范围设计变化时明确列为新切片。
5. 跑针对性验证和仓库要求的最终检查；已通过的完整验证无新代码变化不反复重跑。纯 Markdown 指导改动做路径/内容/差异检查即可。
6. 更新真实完成状态，一主题一个提交，提交说明署名实际模型贡献；不混入 `.zcode/` 或用户进行中的修改。
7. 同任务连续失败三次，停止重试并说明复现、尝试和阻塞；不能靠放松断言让测试变绿。

**交付判据**：功能行为、生命周期、异常恢复和测试证据同时齐全才算完成；设计写过、模型说通过、文件写入成功都不能替代验收。
