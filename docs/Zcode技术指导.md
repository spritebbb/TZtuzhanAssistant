# Zcode技术指导

编写：Codex，2026-09-07。用途：ZCode 接手 M9 后续实现；Codex 负责独立审查、验证、定位与修复 bug。

> 完整化修订：第 1–12 节保留切片背景和总路线；第 13 节起补充可执行契约、参数、遗漏路线和验收案例。存在精度差异时，以后面的具体契约为准。技术方案完成不等于代码已实现；延期只限制实施时机，不再用来代替设计。
>
> 2026-09-07 拍板补充：ZCode（GLM）经用户逐项确认 7 项决策并填入配套技术默认，见第 17 节汇总与各节内「2026-09-07 拍板/补充」标注。
>
> 2026-09-07 实施更新：波次 0 已由 Codex 完成。P0-01A=`ad110c2`、P0-01B=`9bbc224`、P0-02=`63c80b4`、P0-03=`a17471e`、P0-04A=`9720fca`、P0-04B=`0f2b64b`；P0-04C1 已在 DeepSeek 与 SiliconFlow 两个真实端点各运行 3 个合成样本，C2 因未配置 Tavily/Bocha 凭据按合同标记 unavailable。各切片针对性测试与当时全量聚合均通过。后续接手不得重做这些切片，先审实际接口和回归。
>
> 2026-09-08 实施更新：P1、P2 已由 Codex 完成。P1-01=`9b8d39b`、P1-02=`87090f1`、P1-03=`7e9c12e`、P1-04=`b1b2c87`、P1-05=`1d25d0f`；P2-01=`88e6c28`、P2-02=`b8bb8c1`、P2-03=`36bc59b`、P2-04=`6d99a18`、P2-05=`b6b3f2b`、P2-06=`469bafc`。全量测试发现并修复开放约定看板计数（`8add635`）及应用内时间 tick 共享连接事务竞争（`58a6c28`）。P2 完成时 schema 为 v21；后续接手应以本文“实施结果”和代码中的当前版本为准，不得重复实现 P1/P2。
>
> 2026-09-08 P3 实施更新：P3-01=`1db8435`；P3-02A=`a438861`、P3-02B=`5164323`、P3-02C=`d7cc2c2`。全量测试发现情绪门控测试存在固定历史时间与墙钟混用，已由 `5df95bf` 消除漂移。当前 bot.db schema v22；P3-01/P3-02 不再是待实现工作。
>
> 2026-09-08 体验加量启动（用户拍板）：**本文档全部未实现路线（L01–L08/L16、G01–G04、F01–F07、P3-05、§17.1–17.3 及阈值加量）不留最小实现、按完整设计+加量标准实施**。执行调度（13 个批次的划分、依赖序、执行口径、状态回写）统一收敛到 [EXPERIENCE-UPGRADE-PLAN-2026-09-08.md](EXPERIENCE-UPGRADE-PLAN-2026-09-08.md)；本文档仍是唯一权威设计源，各节设计条款逐条有效，落地状态以调度文档状态表为准，本文正文不再逐节改状态。执行口径（节奏/开关/素材生产/LLM 预算/L06 追加拍板等 10 项）以调度文档 §0.1 为准。已先行落库：体验收口四片（`028034d`/`2b34e90`/`76f320b`/`a4a38e9`）、日程素材加量（`07498a2`）、presence 标签修复（`e4e2e9f`）。

## 1. 接手边界与当前基线

用户最新分工：**ZCode 做功能实现，Codex 审查完成后的代码并查找、修复 bug。** 本文是实施任务书，不是已经完成的功能说明。后续不由 Codex 自动继续大批功能开发；用户将本文交给 ZCode 后，按下述切片逐项实现、逐项交审。

权威文档：

- [TECH-PLAN.md](TECH-PLAN.md)：架构原则、生命周期、退出标准。
- [M9 设计与缺陷审计](M9-DESIGN-AND-DEFECT-AUDIT-2026-09-07.md)：产品决策与完整需求。
- 本文件：公开接手纪律、edit 权限、VERIFY 协议和各切片实施结果的权威入口。
- 本机协作信箱与代理配置属于私有工作文件，不随公开仓库发布；缺少这些文件不影响构建或运行。

已完成基线：

| 项目 | 状态 |
|---|---|
| M8.3–M8.7 收尾 | `f4d39ea` 已提交，不再是待提交工作树 |
| M9-00 识图事实层 | `6081d67` 已提交，勿重做 |
| 后端 | 93/93，Codex 实跑聚合测试，504.36 秒（2026-09-08） |
| 前端 | Vitest 69/69；vue-tsc 与生产构建通过 |
| 浏览器 | Playwright 7/7，临时数据目录 |
| 人格 | 现有 golden set 48 场景；签名/魅力专项尚未建设 |
| bot.db schema | `backend/core/userdb.py` 当前版本 22；其他数据库有各自版本 |
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
- 关系阶段要求信任和亲密同时跨过 25/50/75；关系分支可看简短描述与形成原因，不显示等级或进度条。
- 数据加密采用 Windows 本机便捷解锁，并提供独立恢复口令换机；D8 采用 Cloudflare Tunnel + Access。
- IM 顺序先 QQ 后微信；QQ 走官方机器人低风险路线，微信按用户决定采用个人微信本地桥接。

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
| P0-01 | 输出卫生出口 | 已完成：A=`ad110c2`，B=`9bbc224` |
| P0-02 | 周期备份加固 | 已完成：`63c80b4` |
| P0-03 | 行为签名 eval | 已完成：`a17471e`，53 条离线样本 |
| P0-04 | 槽位路由 + 模型/搜索实测 | A/B 已完成：`9720fca`/`0f2b64b`；C1 已实测，C2 缺凭据 unavailable |
| P1-01 | 人格侧写、正典与编译切片 | 已完成 `9b8d39b` |
| P1-02 | 语境注册表渐进接入 | 已完成 `87090f1` |
| P1-03 | 情绪状态基础 | 已完成 `7e9c12e` |
| P1-04 | 行程状态机与时间 tick | 已完成 `b1b2c87`；并发修复 `58a6c28` |
| P1-05 | 纪念日/季节换挡 | 已完成 `1d25d0f` |
| P2-01～06 | 二维关系、校准、对称约定、链式事件、节奏仪式、重逢 | 已全部完成 `88e6c28`～`469bafc` |
| P3-01～05 | 全情绪矩阵、知识内化/求证、TTS、数据安全、演化/遥测 | P3-01/02 已完成；P3-03～05 待后续独立交付 |
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
2. 到期依据落在最近成功 manifest 的时间，启动时检查是否超过一天，而不是每次进程启动重新计 6 小时。首次有数据但没有成功包时补一次。失败不更新成功时间，重试退避。（2026-09-07 拍板确认：每日成功一次、保留 7 份，回溯窗口约 7 天。）
3. `backup()` 写到唯一 `.partial` 目录；每个 SQLite 通过 backup API 创建独立一致快照，integrity_check 后记录大小和校验和；媒体复制结果与遗漏明确计数。
4. **多库限制要诚实**：单库 backup 不保证三个库是同一业务时刻。先交付标明起止时间、逐库一致性的可恢复包；若需要承诺跨库原子恢复，另加覆盖所有写路径/进程的短时写入协调与快照边界，不能仅套一个线程锁就声称原子一致。
5. 所有必需项目成功后写 manifest、原子改名完成；轮转只处理匹配本模块格式且验证成功的包，不清理 schema-* 快照，也不在新包失败时删旧成功包。恢复只写新的空目录，拒绝覆盖在用库。
6. 在临时恢复目录打开数据库、校验 schema/计数/代表性引用，验证 E03 恢复仍独立有效。增量丢媒体或导出范围不完整时报告 partial，而非 success。
7. 恢复只经 CLI（2026-09-07 拍板，不做恢复 UI）：新增 `scripts/restore_backup.py`，子命令 list/verify/restore；restore 显式 `--data-root` 与 manifest，默认 dry-run 预览，`--apply` 才落盘且只写新的空目录，完成后打印校验结果与手动切换指引。设置区不承载恢复流程。

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

**A 路由骨架**：修改 `core/llm.py`、`config.py`；新建 `core/model_routes.py`、`tests/test_model_routes.py`。建议 `resolve_route(task, explicit_model=None) -> Route`，任务键为 chat_routine/chat_deep/tool/batch_diary/batch_other/judge/vision。这是“六类职责、七个任务键”，日记从批处理拆出，不必纠结字面六或七。2026-09-07 补充：另设第八键 `extract` 承载结构化提取（见 13.7），未单独配置时兼容复用 batch_other 端点。

- 首次解析旧 LLM_*/LLM_PERCEPTION_*/VISION_* 配置，保持旧优先级和显式 model 参数语义；新槽位未配置时走兼容分支。
- 客户端缓存按端点+认证配置标识隔离，禁止日志打印 key；降级链检测环，按错误类型限定重试次数。
- 所有路线计入真实 model 和任务通道；中途已经开始流式输出不悄悄切模型拼接半句，失败按整个候选重试策略处理。
- 单测 mock 所有客户端，覆盖缺槽位、显式覆盖、端点切换、限流、失败回退、循环配置、用量归属。

**B 离线实验工具**：新建 `scripts/benchmark_model_routes.py`、`scripts/benchmark_search_providers.py` 及 tests 下的 mock 测试。工具默认 dry-run，显式 `--live` 才联网；参数要求样本数、并发、总调用上限、预算、输出位置。支持断点续跑、失败计数和输入散列，避免重试重复收费。

**C 真实实验**：在已授权且配置可用的端点上执行。M9 文档的模型名只作历史候选别名，先核验支持的实际 id，不写死不存在的模型；没有接入的候选标 unavailable。Tavily/Bocha 用同一合成查询集比较中文/英文/时效/冲突；展示覆盖率、延迟、结果域名分布及成本，不按返回条数判断质量。采样预算与密钥准备缺失时仍交付 A/B，不虚构 C 结论。真实结论经过查看再更新路由配置，不因旧文档榜单自动切换生产模型。实测授权（2026-09-07 拍板）：单次实测总额硬上限默认 ¥30（配置可调），并发≤2、每调用网络重试≤2；断点续跑键保证不重复计费，报告含逐笔费用与总额，密钥由用户提供。合成查询集由 ZCode 构建（2026-09-07 补充）：≥40 条，覆盖中文/英文/时效/冲突四类，存 `backend/evals/fixtures/search_queries.jsonl`。范围拆分（2026-09-07 拍板）：当前仅主力+廉价两个 OpenAI 兼容端点可用、无 Bocha/Tavily key——C 拆 C1 模型实测（可做）与 C2 搜索实测（推迟至 P3-02 开工前配 key）；路由默认映射按 OpenAI 兼容双端点设计。

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
6. 内容生产流程（2026-09-07 拍板）：ZCode 依现有人格卡、M8 物料与既有语料起草九节、正典与变体池全量初稿 → Codex 做内容一致性审查（与既有设定互查）→ 用户终审定稿 → 才进入编译接线提交；定稿前不接 `build_system_prompt`。素材来源（2026-09-07 拍板）：仓库内物料 + 用户口述补充，初稿标注待口述位；首份只写菟菚，资源目录结构按多人格设计（计划多人格）。

**数据**：首期版本化 JSON/Markdown 资源，不建数据库；每条带 format_version/source_namespace。只静态内容则无需 reset；任何用户教学生成的覆盖另归 G01/P2-02，不偷偷写回核心文件。

```text
VERIFY: .venv/Scripts/python.exe tests/test_persona_slices.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py ;; .venv/Scripts/python.exe tests/test_persona_switcher.py
```

### P1-02：语境注册表，先迁一处再扩

**新建** `backend/core/context_registry.py`、`tests/test_context_registry.py`；**修改** `pipeline.py`、`explainability.py`、`kv_registry.py`。首期固定 `colists.list_context`（2026-09-07 补充，不再二选一），既有共读/记忆召回先不动。

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
2. 设计持久化认领，建议独立 `job_runs(scope_key, job_key, period_start, status, lease_until, attempt, finished_at)`，唯一键为 scope/job/period。以事务 CAS 获取 lease，过期可恢复；运行时记录不随 E03 导出。表落哪个库须在 ADR 中明确并覆盖对应 reset。补充默认（2026-09-07）：`job_runs` 建议 bot.db（与关系状态同库，随主库迁移/备份走），ADR 确认后执行；计划任务名固定 `TZtuzhanAssistant-TimeTick`，每小时执行 `python -m backend.maintenance.time_tick --data-root <显式路径>`。
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

**实施结果（`88e6c28`，schema v17）**：`users.trust/intimacy` 首次仅从旧 affection 回填 NULL，后续由 `relationship_dimension_ledger` 按来源事件和规则版本幂等记账，单维分别执行每日正负上限；兼容 affection 始终同步为 `min(trust,intimacy)`，旧消费者自然遵守双门槛。`affection.py` 提供事件应用、反向 delta 撤销、阶段/子阶段派生，`state.py`、`behavior.py`、人格切片、解锁、成长展示、关系快照和导出恢复统一消费真实两维。8 个子阶段时刻经既有解锁队列自然表达，不在收藏页暴露数值。迁移、reset 和兼容规则见 `docs/adr/M9-relationship-dimensions.md`；`tests/test_relationship_dimensions.py` 及关系包、版本、快照、解锁回归覆盖旧用户迁移、幂等、日限额、撤销和双阈值边界。

### P2-02：用户教学、侧写与双向校准

**接入** `daily.py` 的提炼、`pipeline.py` 用户画像段、`behavior.py`；新建 `core/user_preferences.py`、`tests/test_user_preferences.py`。复用既有记忆管理/纠偏入口，不加面板。

建议 `user_preferences(id,user_id,category,value_json,origin,source_message_id,confidence,status,created_at,revoked_at)`，类别先限定安慰偏好、称呼范围、提醒强度、玩笑禁区。明确教学与模型观察分层，不把“嗯”自动归类成不耐烦或把状态猜测存成心理诊断。

步骤：自然语言提取候选 → 显式偏好走可追溯保存、模糊观察标低置信度 → 聊天可查看/撤销 → 编译为短行为约束 → 撤销当轮及后续失效。新偏好与旧称呼/提醒配置发生冲突时用一条权威 resolver，不能双入口覆盖来回跳。用户反馈“不喜欢这样哄”可降低该策略权重，但临时轮不更新策略学习。表进迁移、双 reset、导出与源删除路径；撤销不残留向量。默认不开永久敏感侧写。

```text
VERIFY: .venv/Scripts/python.exe tests/test_user_preferences.py ;; .venv/Scripts/python.exe tests/test_memory_correction.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

**实施结果（`b8bb8c1`，schema v18）**：`backend/core/user_preferences.py` 实现 comfort/address/reminder/humor 四类封闭偏好的 `candidate → active → revoked` 生命周期，显式用户教学可确认，推测性表达不落画像；同值教学幂等并可复活。resolver 按“明确禁止、最近确认教学、旧配置、默认值”合并为短行为约束，`pipeline.py` 在正常对话注入，撤销后自动回退下一优先级。`memory_admin.py` 提供列表、新建、确认/更新和撤销 API，所有写入带来源消息、置信度与 CAS 状态保护；旧称呼配置按需迁移。`daily.py` 同时接入 P2-01 的确定性语义事件提炼，一天最多记一次明确披露，冒犯不自动扣分。测试覆盖临时对话零写入、记忆纠偏联动、偏好冲突与撤销、跨人格和关系包。

### P2-03：对称约定，先把她说过的话记清楚

**修改** userdb promises、`daily.extract_promises`、`initiative.maybe_follow_up_promise`、relationship_events；新建 `tests/test_symmetric_promises.py`。

给 promises 增加 owner（user/assistant）、来源、到期、status，旧行 owner=user。区分模糊客套和可执行约定；只有明确承诺才入账，角色虚构约定必须标命名空间。assistant-owner 的未来任务要链接实际可执行能力/日程，不能说已做但无执行记录。完成/取消/到期由状态机推进，用户取消不当失败；她未做到可坦白并提供修复，不向用户扣分。完成事件和提醒都按 promise id 幂等，删除源同步失效。迁移/导出按新字段兼容。

```text
VERIFY: .venv/Scripts/python.exe tests/test_symmetric_promises.py ;; .venv/Scripts/python.exe tests/test_promise_followup.py ;; .venv/Scripts/python.exe tests/test_relationship_events.py
```

**实施结果（`36bc59b`，schema v19）**：`promises` 增加 `owner`、可空 `due_at`、`action_kind`、`namespace`、`source_message_id` 与规范化内容哈希，旧 pending 行迁移为 user-owner/open。`daily.py` 提炼时要求所有者和期限；assistant-owner 若没有可执行 action 只作为叙事约定，不谎称后台任务。读取时惰性推进过期，取消和过期均不扣关系；user-owner 完成按 promise id 仅增加一次 trust，assistant-owner 完成不把她的履约算到用户身上。`initiative.py` 对她自己的逾期约定生成坦白进度或补救分支，并继续走共享主动额度。全量回归发现看板只统计旧 pending，已在 `8add635` 改为同时统计 pending/open。

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

**实施结果（`6d99a18`，schema v20）**：`backend/core/event_chains.py` 首版只注册“明确约定完成 → 一次正向回望 → 关闭”规则，`event_chains` 按 user/source/rule 唯一，实例保存 due、attempt、expiry 和单向状态。到期节点转成既有 `pending_thoughts` 的 `chain_aftermath` 候选，由统一仲裁、共享额度和投递回执处理；成功表达关闭实例，失败在同一实例累加，最多 2 次尝试或 7 天过期。永久取消、源删除和 reset 都阻止重建；代码中没有负向链，用户缺席或取消不会产生惩罚。`tests/test_event_chains.py` 覆盖幂等创建、到期、投递、失败熔断、取消、删除和 reset，并回归心事与主动仲裁。

### P2-05：追发、主动收尾、称呼和晚安仪式

原设计分为 A 有期限的追发、B 晚安/行程收尾、C 称呼变化三个子项；最终在同一有界提交中落地，因为三者共享一套用户轮次判定和来源生命周期。文件落在 conversation_rhythm/initiative、pipeline 快捷告别路径和用户偏好 resolver，新增 `tests/test_conversation_rhythm.py`。

- A：追发有 origin_turn_id、expires_at、最大一次表达；用户开始新话题、手动停止、勿扰或来源删除时取消。复用候选及原子额度，不用内存 sleep 挂一个不可恢复定时器。
- B：显式晚安先遵守用户结束意图，只给一段简短回应，不趁机追问。行程“去忙”是角色表达，用户需要帮助时基本可及；不能以睡觉为由锁死输入框。
- C：称呼来源可查看/撤销，先使用用户批准的范围，关系阶段变化只提供候选而非强行越级昵称；变化若落事件必须说明来源。
- 分段气泡/打字停顿为独立前端呈现切片后置（2026-09-07 拍板），后端先行落协议字段：SSE 新增 `segment` 事件携带 `{logical_message_id, segment_index}`，chunk 流与 `done` 最终全文不变，归档/刷新按 logical message id 重组；段是呈现事件，不是新消息、不是新好感来源。人工延迟可关闭且中止即取消，不用“生气故意不理人”制造惩罚。

**数据**：优先复用心事/偏好存储，仅增加必要类型注册，不另建孤立计时系统；任何新 kv 登记。

```text
VERIFY: .venv/Scripts/python.exe tests/test_conversation_rhythm.py ;; .venv/Scripts/python.exe tests/test_proactive_policy.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py
```

**实施结果（`b6b3f2b`）**：

1. `backend/core/conversation_rhythm.py` 成为节奏状态的权威模块。`rhythm:followups` 以版本化 kv 持久保存有界追发，记录 `origin_turn_id`、`source_message_id`、`ready_at`、`expires_at`、状态和一次性表达标记；该键已登记到 `kv_registry.py`，最多保留一条有效候选，默认 20 分钟后可表达、2 小时过期。
2. `pipeline.py` 在用户消息已落库后调用 `handle_user_turn`：显式晚安和手动停止立即取消追发；打断语从紧邻的上一条 assistant 消息创建候选；明显的新长话题取消旧候选。显式晚安只注入简短收尾约束，保持输入和后续新会话可用。
3. `initiative.py` 通过 `_maybe_rhythm_followup` 把到期追发送入既有统一二级仲裁。勿扰时取消时效性候选，源消息不存在、已删除、已表达或已过期均不投递；成功表达后状态关闭，失败不制造第二条候选。
4. 称呼解释复用 `user_preferences`。`address_candidates()` 返回真实偏好 id、来源类型、来源消息 id、简短原因和 `revocable`，`GET /api/memory/address-candidates` 只暴露候选与依据，不显示等级或进度条；撤销继续走既有偏好生命周期。
5. `tests/test_conversation_rhythm.py` 覆盖创建、等待、过期、单次表达、新话题/晚安/手动停止/勿扰取消、源删除、称呼来源和在场文案；同时回归主动额度、关系包和人格评测，确认没有旁路投递。

### P2-06：M8 重逢三段式

**复用** `offline_narrative.collect_offline_context`、`greeting.greeting_for`、`daily.write_daily_diary/maybe_write_research_report` 与关系事件；新增 `tests/test_reunion_flow.py`。

1. 久别窗口产生 reunion_id（按人格、最近离开锚点），第一条只由可追溯的真实记录/明确角色生活记录构成。没有离线数据就诚实留白，不编造“我刚去真实城市旅行”。
2. 第一条投递后状态从 pending 到 offered；用户可回应，也可直接换题，不能卡在必须回应才能聊天。禁止问去了哪、为什么不来，不扣好感。
3. 只有用户真正回应，才将双方这一轮补入日记素材；若未回应，只能记已真实发生的问候，不能生成“我们聊了这些”。研究补写仅在有研究素材时调用。
4. 一次窗口只发一条，网页刷新、多个窗口与失败重试不重复。实施采用关系表 `reunion_arcs` 保存 version/id/status/source_ids/timestamps，不复制原文，并明确纳入导出/reset；源删除后不能恢复旧叙事。
5. 与恢复预览、封存和版本快照分离：打开旧关系包不会自动触发一段已发生的重逢，不修改 M8 的只读/显式确认契约。

```text
VERIFY: .venv/Scripts/python.exe tests/test_reunion_flow.py ;; .venv/Scripts/python.exe tests/test_offline_narrative.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

**实施结果（`469bafc`，schema v21）**：

1. `backend/core/reunion.py` 与 `reunion_arcs` 表承载重逢状态机。记录按人格作用域保存指向生活事件的 `source_snapshot_id`、`offered_message_id`、`response_message_id`、状态和时间戳，不复制离线叙事正文；状态只按 `pending → offered → responded → closed` 或 `expired` 前进，7 天限频并在 7 天后过期。
2. 唯一合法来源是 P1-04 已落库的 `character_life_events`。`greeting.py` 先建立或复用 arc，再用来源描述生成不超过 180 字的她视角问候；没有有效来源时明确要求模型诚实留白，生成失败采用“回来啦。好久不见。”，不编造现实经历、不追问用户去向。
3. 问候实际可见后，以 bot 消息写入 `messages`，再把 arc 标为 `offered` 并绑定真实消息 id。`pipeline.py` 只观察该问候后的第一条用户消息：真实回应标为 `responded`，明确换题直接 `closed`；两条路径都不改信任、亲密或旧好感兼容值。
4. `daily.py` 只在日记真实消费到回应后关闭 `responded` arc；没有研究素材时不额外调用研究生成。来源生活事件被删除时 arc 关闭，防止旧叙事再次出现。
5. `relationship_export.py` 将生活事件和 arc 纳入导出，恢复时先重映射来源 id，再强制将 arc 置为 `closed` 并清空消息引用，因此导入旧关系包不会自动重演重逢。`userdb.py` 初始化迁移、通用 reset 与 `reset.py` 都覆盖新表；设计与回滚约束记录在 `docs/adr/M9-reunion-arcs.md`。
6. `tests/test_reunion_flow.py` 覆盖真实来源、无来源留白、投递状态、回应/换题、限频、过期、源删除、日记收束、导出恢复不重放及无关系扣分；并回归离线叙事、关系包、schema 备份和快照版本。

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

**实施结果（`1db8435`）**：

1. `emotion_state.py` 保留七类离散情绪为唯一情绪来源，将五轴基础向量按活跃强度确定性合成；信任主要修正防备与耐心，亲密主要修正柔软和追问意愿，全部夹紧至 0–1。深夜/低精力只降低追问，不借关系值升级亲密。
2. `attitude_instruction()` 把矩阵编译成短行为指令，不输出轴名、数值或心理理论。受伤/戒备与 tenderness 可同时保留；低亲密追加明确的称呼和暧昧边界；任何状态最后都保留菟菚直白、克制、具体关心人的人格签名。
3. `AgentState.attitude_axes` 是只读派生属性，不建表、不写 kv。`behavior._emotion_line` 保留原三个高价值锚点并消费完整矩阵；相同状态连续 30 轮输出确定，不产生隐式个体演化。
4. 人格切片 `state_keys_version` 从 1 升到 2，封闭键增加 `attitude.patience/humor/guard/directness/followup`，仅支持 0–1 的 gte/lte。状态视图由现有 emotions/trust/intimacy/energy/time 派生这些键，资源和测试同步升级；无情绪仍完全走旧行为路径。
5. `tests/test_attitude_matrix.py` 覆盖七类向量、双维独立修正、高信任低亲密不越级、矛盾情绪、低精力边界、运行时无理论泄露和 30 轮稳定性；人格签名、状态修复、情绪衰减和切片编译回归通过。

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

**实施结果（A=`a438861`、B=`5164323`、C=`d7cc2c2`，schema v22）**：

1. A 在既有 `tool_loop.py` 原生/文本双循环上增加取消钩子、每个工具循环总计最多 8 次真实执行和规范化调用指纹。每个原生调用仍生成独立 `tool_call_id` 并与 tool 消息一一配对；同一循环重复 name+args 复用首次结果，不再次触发写入或外部副作用；文本回退改为可取消的顺序执行。进度事件只报告 thinking/tool/tool_done，循环结束后单独生成最终人格表达。
2. B 新建 `source_verification.py`。查询经 NFKC、小写和空白规范化；URL 去 fragment/跟踪参数，站点按注册域近似归一，重复 URL、同站子域和明显同文转载只算一个来源。正常路径选择两个独立站点；同实体、指标、单位和时期的结构化 claim 数值冲突时加入第三站，仍冲突则返回 conflict 并要求并列，不可比较项标记 not_comparable。
3. `search.py` 对价格/天气/汇率/当前版本使用 2 分钟缓存，对新闻/最新/今天使用 5 分钟，普通查询最多 24 小时；结果携带 provider、fetched_at 和 cache_hit。`intent.requires_search` 确定性识别时效问题。pipeline 和 web_search 插件消费 supported/insufficient/conflict/failed 报告，保留 URL，把来源包装为不可信材料；缓存结果不宣称“刚查到”，失败和证据不足不伪装成一致。
4. C 新增 `knowledge_opinions` 与 `knowledge_opinion_sources`。观点保存 stance/origin/confidence/version/status，来源表保存 document/chunk 的片段范围及 hash；同一规范化观点+来源幂等，撤销/复活推进版本。读取重新验证用户、文档、分块、范围和 hash，源变更立即停止注入；观点不写入 facts、long_memory 或用户画像。
5. `KnowledgeOpinionsProvider` 只在当前查询与观点相关时进入 context registry，并继续受统一预算、sticky/cooldown 和成功回合提交控制；渲染明确它是角色观点，不是用户事实或外界证明。知识 API 新增观点列表、文档下创建和撤销；原始 kb 召回提示也改为带文档来源的不可信资料，不再说成她已经形成的观点。
6. schema v21→v22 只新增两张空表和索引。两张表加入 userdb/reset 清单；关系包 knowledge 类别按 document→chunk→opinion→source 恢复并重映射三层 id；删除文档先删除观点来源和观点。迁移与回滚见 `docs/adr/M9-knowledge-opinions.md`。
7. 新增 `test_tool_execution_contract.py`、`test_source_verification.py`、`test_knowledge_opinions.py`，覆盖 A/B/C 的取消、去重、来源独立、转载、冲突第三源、缓存、失败状态、观点生命周期、语境、hash 失效、API、导出恢复和删除。最终后端 93/93、前端 69/69、vue-tsc/生产构建、Playwright 7/7 全绿。

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
5. 2026-09-07 用户已选“本机便捷解锁 + 独立恢复口令”；具体密钥层级、迁移与验证以第 21 节为准。真实资料迁移仍必须先完成临时目录 PoC、备份验证和 Codex 审查，不能边覆盖边试。

```text
VERIFY: .venv/Scripts/python.exe tests/test_data_protection.py ;; .venv/Scripts/python.exe tests/test_schema_backup.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

上述验证针对选定方案的临时目录实现；只有 ADR 阶段时检查文档和 PoC，不声称完成加密测试。

### P3-05：演化日志与本地质量统计

拆 A 白名单演化、B 可选本地统计。新建建议 `core/persona_evolution.py`、`core/experience_metrics.py`、`tests/test_persona_evolution.py`、`tests/test_experience_metrics.py`；接 behavior、关系事件与既有解释/成长入口。

- A：日志字段 user_id/source_event_id/parameter/old/new/rule_version/reverted_at；参数仅限成长层白名单，小步上限、冷却、显式可撤销。身份、安全、隐私和权限不在白名单。撤销重新计算后续有效演化，不简单把数值写回很久前的 old 覆盖新变化；源删除后对应偏移失效。白名单首版（2026-09-07 用户拍板：仅表达层）：`humor_usage_rate`、`verbosity_preference`、`initiative_template_weight` 三参数。拍板理由：仅表达层可控、可测、可回滚——三参数都能被 P0-03 eval 直接测量，漂移可被发现，回滚无副作用；话题兴趣不必现在决定，待 eval 体系具备话题分布测量能力后再版本化加进白名单。
- B：只记录必要统计（延迟、失败规则、重复率、来源选择、用户明确反馈），默认本地；不把所有日记/聊天当“遥测”再复制一份，不默认上传。临时轮不写统计，用户能关闭和清理；多代理测试数据不污染真实统计。
- 质量面板复用现有入口；一次统计变化不自动改人格。把问题候选交审，不让系统自己以留存/依赖程度为目标调参。
- 新表须独立 ADR、schema/reset/导出策略。演化关系历史可导出；运行质量统计通常不属于关系包，明确排除。

```text
VERIFY: .venv/Scripts/python.exe tests/test_persona_evolution.py ;; .venv/Scripts/python.exe tests/test_experience_metrics.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

## 8. 原审计五个空白：完整设计后分片实现

这里给出可实施路线。确定性阈值和接口已经在第 13–22 节固定；它们属于工程默认而非用户偏好，实施时如有证据需要改变，ZCode 必须在回信列明并由 Codex 审查。

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

实施仍延期：D8 公网/推送、STT、桌面宠物、多角色群聊；完整技术方案分别见 L12、L09、L10、L15。网页/EPUB、观察日志/世界观共创的方案见 L01/L02。训练音色、真实模型实测、加密真实迁移等有外部条件的阶段，缺条件只标该阶段未完成，不阻塞同主题可以离线交付的 adapter、工具、迁移演练与测试。

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

## 13. 完整方案的共用契约（以下任务全部继承）

### 13.1 适用范围与完成定义

**拟真总则（2026-09-07 用户认可，对所有切片生效）**：所有状态机制（两维/小档/情绪向量/态度矩阵/季节等）只作阴影调色——运行时产物禁止出现理论术语与数值；内容供给（行程/日记/研究/关系事件等「她的生活」）优先于状态 machinery，机制不得替代内容；模板率（P0-03）为动人格切片的合并门槛，模板率上升即返工。

**拟真取舍四定（2026-09-07 用户拍板）**：
1. **情绪即时反应**：状态影响即时表达（今晚被怼下一句就冷淡），沿用现有情绪半衰期＋行为帧缓冲设计；不建额外缓冲层，不搞负向延后处理。
2. **静默自然解锁**：小档/里程碑的新话题、称呼候选由她在对话中自然用出，不做 UI 提示、不做解锁清单、不主动宣告「我解锁了X」；成长页仅保留三档进度（14.10.1）。
3. **极慢演化漂移**：三个表达参数每周期最多一档（如幽默频率偶尔→经常需数周真实样本），漂移无感但不突兀；P3-05 落地时定具体周期与冷却。
4. **话题自主生发**：小档话题池由正典预写（主渠道）之外，允许她从自己的日记/研究/生活事件里**自主生发新话题**——须过 OUT-1 卫生检查与话题池降权机制（被冷落话题自动降权，不追问）。来源限于她自己的 character_fiction 素材，禁止生发用户隐私相关话题。

本版覆盖：TECH-PLAN 的 M0–M8 未完条目、18 个 Epic 的后续扩展、Q1–Q5；M9 设计中的 4.1–4.10、26 个缺陷、五项设计空白、用户功能反馈。已完功能保留接口与回归，不无故重新实现。正文明确“不搬”的竞品功能不纳入实现目标，但必须在覆盖表记为不采纳，不能混为遗漏。

每项方案由原章节 + 对应详细补充 + 本节共同组成，至少明确：目标与非目标、现有/新文件、接口输入输出、存储/迁移、状态流转、失败恢复、隐私/删除/隔离、测试输入与预期、开关与回滚。后续 ADR 是把本方案登记为实际迁移，不是要求 ZCode 重新设计整个功能。

下文数字是**工程默认值**，除特别注明外不是用户拍板的不可变产品常量。集中配置、记录 rule_version，测试可注入时钟和参数；实测调整需写理由，不能在多个模块散落不同阈值。

### 13.2 接口与实体约定

- 新 API 位于 `/api/`；列表默认 limit=20、上限100、稳定游标分页。读取返回 `{ok:true,items,next_cursor}`，单项 `{ok:true,item}`；不要改既有 API 的外层结构。
- 变更请求携带 `request_id`，更新已有资源携带 `expected_version`。成功返回资源 id/version；重复相同请求返回原结果，不执行第二次；同 id 不同 body 返回409。校验400/422、未认证401、越权403、跨人格资源不可见404、版本冲突409、额度429、依赖不可用503；不得把原始异常/密钥写进响应。
- 所有新增业务表默认拥有 `id INTEGER PK,user_id TEXT NOT NULL,created_at,updated_at,version INTEGER DEFAULT 1`；业务唯一键显式列出。时间存 UTC RFC3339，日历计算用用户时区（现有部署 Asia/Shanghai）。本地时间不要直接作为全球唯一幂等键。
- JSON 用 Pydantic/等价结构校验、拒绝未知执行字段；有上限，普通短文本200字、备注2000字、一般正文20000字，特殊文件走上传限制。所有 SQL 参数化，不能把前端/模型传来的表名字段名拼 SQL。规范化哈希的固定操作顺序（2026-09-07 审查 I6 定序，适用于 promise_hash/topic_key/evidence_hash 及后续一切文本规范化键）：NFKC → lower → 去标点 → 压空白。
- 新接口的 user_id 从认证/人格上下文解析，不相信 body 的 arbitrary user_id。后台显式绑定作用域，HTTP 当前人格切换不能改变已经认领任务的目标。

### 13.3 生命周期包 LC-1

新增表默认进入：bot.db 当前版本 +1；增量迁移；新库 schema；两处 reset；E03 分类导出、预览校验、恢复主键映射；源删除/更正；向量一致性回归。各任务列出的“持久/运行时”决定导出政策，不是放弃 reset。

持久派生实体保留 `source_type/source_id/source_version`，多来源使用独立 `source_links(user_id,owner_type,owner_id,source_type,source_id,source_version)`，唯一五元组；通用引用解析器只允许注册类型。源删除先阻止读取，再使派生状态失效，最后异步清索引；失效标记不得保存已删原文。删除用户派生记录不反向删除源事实。单事务可完成的修改同事务；跨 SQLite/向量用可重放清理队列，读时再验源，索引失败也不暴露已删内容。

本节不授权把所有未来表一次建齐；只有切片实际使用的表才迁移。关系包 format_version 仅在顶层契约改变时升级，数据库版本按各库独立管理；旧包缺新增类别按空集合处理，新包不能无校验忽略不认识的重要字段。

### 13.4 后台任务包 JOB-1

`job_runs` 采用 P1-04 的持久认领：`scope_key,job_key,period_start` 唯一，状态 pending/running/succeeded/failed/cancelled，附 lease_owner/lease_until/attempt/next_retry/reset_epoch。首期租约120秒、每30秒续租、单次超时60秒、最多3次、退避1/5/30分钟；较长操作显式覆盖，最长600秒。

同一用户同类写任务串行；CAS 更新使用 owner/epoch，旧进程失去租约后的返回值丢弃。成功事务写结果与 succeeded，取消/重置提升 epoch，迟到模型结果不能落盘。尝试次数按真实调用计，不按空轮询计。临时对话不创建持久 job。（2026-09-07 预研补充：租约 CAS 有仓库内先例可照抄——`backend/agent/session.py:_claim_running` 的单条 UPDATE＋rowcount 判定模式；跨进程写回须数据库侧校验 reset epoch，core/reset.py 的进程内 user_write_guard 不覆盖计划任务进程。）

### 13.5 用户可见产物包 OUT-1

产物生成流程固定为：验证授权来源 → 冻结 source versions → 调用表达模型 → 输出卫生检查 → 再验源/epoch/version → 落库或返回草稿 → 投递。正文有修订则旧草稿返回409，不自动覆盖；失败只回退有据内容，不把 fallback 说成模型实测成功。

**草稿不是关系事实**：默认保存在客户端内存；需续作的草稿显式标 draft、7天到期且不入召回。确认后才生成观点/活动/作品。用于发送的结构性指令不出现在用户气泡；同一 logical_message_id 的最终内容统一用于 SSE done、归档与 TTS。

### 13.6 验收包 QA-1 与关闭策略

每个新增状态至少测试：新库、旧库迁移两次、正常顺序、重复请求、版本冲突、并发认领、失败重试、源删除、两人格、临时轮、reset、导出→空目录恢复。只读纯函数只测适用项，但需在报告标“不适用”原因。无需为了低风险文档写业务测试。

每项新功能配置 `FEATURE_<任务语义>_ENABLED`，**双层口径（2026-09-07 ZCode 建议随审查定稿）**：部署级默认走 config 的 env 层（既有 STICKER_ENABLED 等同构风格）；用户可调开关登记进 `backend/core/features.py` 的 FLAG_DEFAULTS（**默认 False**，经 feature_flags.json 持久化——该动态系统已存在，pipeline 有消费先例），每片任务书必须写明本片开关走哪层；设置区手动开启一律指动态层。**两层优先级（审查 I5 收口）：动态层（feature_flags.json）覆盖 env 层——env 仅为部署初值，用户在设置区的改动写动态层并即时生效**。关闭立即停止新候选与注入，用户数据保留且已有数据可导出/删除。迁移只增不破坏，关闭不尝试倒灌旧字段；真正 schema 回滚用验证过的升级前快照，不能用旧版代码盲开新库。

### 13.7 模型提取的统一约定（2026-09-07 补充）

对话内/对话外的结构化提取（偏好候选、情绪成因、关系语义事件、约定 owner、梗候选、话题短语）统一走 P0-04 路由新增任务键 `extract`：廉价、非流式、JSON 输出；`extract` 未单独配置时按兼容分支复用 `batch_other` 端点。输出经 Pydantic schema 校验并拒绝未知执行字段；`confidence`∈[0,1]；失败或超时返回空候选列表并记 rule id，不阻塞主回复、不重试已执行副作用；提取输入只含必要上下文与源 message id，不整段灌历史。各切片的提取 schema 在各自测试用固定样本验证。

## 14. 原有切片中未定细节的确定方案

### 14.1 P0-01 输出卫生的具体出口与重写预算

`HygieneContext(kind,user_requested_explanation,source_namespace,persona_id)`；`HygieneResult(text,action,rule_ids)`，kind 取 chat/proactive/diary/artifact/tts，结构化 extraction/tool-json 不调用该模块。`inspect_reply` 不剥正常代码中的标签，先识别 Markdown fenced code 与用户明确要求引用的范围。

阶段 A 在 pipeline 最后一次 apply_reply 之后、任何 assistant 持久化/stream callback 之前统一 `finalize_visible_reply(candidate,ctx,rewrite_budget=1)`。重写与重复消除共用一次文案生成预算；工具循环结果已经执行的 tool messages 作为只读素材，重写不传 tools。跨块标签以整条候选解析解决，禁止“原样播出再reset”冒充防泄漏。

阶段 B 接口不变，接 `greeting.greeting_for`、initiative 的所有生成者、`daily.write_daily_diary/maybe_write_research_report`、focus 收尾、activities 的 question/viewpoint_draft、dual_perspectives/possibilities/sealing 的草稿/告别信。每次只接一组，维护 `visible_output_channels` 测试覆盖表，漏接项使测试失败。真实思考字段不读取；遇正文混入思考仅拒绝该候选，不把原文写审计。软“助手腔”先评分后限次重写，技术解释例外仍正常通过。

**现状侦查（2026-09-07 ZCode 预研，行号以当日工作树为准，重构后失效）**：三条推流路径均在清洗前推 raw——① 工具循环出口 `pipeline.py:1534`（整段 raw 切片推流，注释明示“推的都是 raw，done 帧是后处理 reply，二者允许差异”）；② 普通流式出口 `pipeline.py:1548`（边生成边推，`_extract_reply/strip_actions/trim_farewell` 在推完后执行）；③ 重复重写出口 `pipeline.py:1583`（先推第一版、RESET 清空、再流式推 raw2）。反向缺陷：插件钩子 `apply_reply` 在 `pipeline.py:1608` 于流式推送之后执行——流式模式下插件改写不出现在用户气泡（显示≠入库）。易漏点：临时轮在 `pipeline.py:1677` 提前 return，卫生检查必须挂在其之前（不留痕但也不许泄漏出门）。下游须取同一文本的消费点：`api/chat.py` 的 done 帧、`db.add_message` 存档、长期记忆双写（“菟菚说：{reply}”）、解锁 `mark_delivered(reply[:300])`、表情包 `maybe_attach_sticker`、解释快照、TTS（`core/tts.py`，前端 tts.ts 以最终文本触发）。**实施形状**：三处推流点收敛为一个——生成只累积不推送 → 结构清洗 → 去重/重写（预算 1、不重跑工具）→ apply_reply → `finalize_visible_reply` → 一次性推送净化分块 → done/持久化/TTS 同源；全缓冲下重写不再需要 RESET 气泡（开关关闭保留旧行为原样）；markdown fenced code 与用户要求的技术解释在 `inspect_reply` 内白名单豁免。

验收新增 `tests/test_visible_output_channels.py`：各通道输入含泄漏标记的固定模型输出，断言所有可观察出口均无标记；同一模型正常技术答复不误判。关闭开关测试复现原出口，但不恢复已删除的 reasoning_content 兜底 bug。

```text
VERIFY: .venv/Scripts/python.exe tests/test_output_hygiene.py ;; .venv/Scripts/python.exe tests/test_visible_output_channels.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py
```

### 14.2 P0-03/P0-04 的可复现实验与路由合同

新增 `EvalRun(id,case_hash,candidate_route,seed,run_version,started_at,status)` 和 JSONL 结果，文件只在指定输出目录；`generate(case,route)` 与 `judge(sample,judge_route)` 分函数，样本A/B顺序按固定seed打乱。确定性维度 pass/fail；主观签名/自然度/潜台词各1–5分且必须返回 evidence_span，不接受没有证据的高分。至少双次随机顺序交叉裁判，冲突>1分进入人工复核列表；不是加总后掩盖红线失败。

单轮/多轮/30轮各有 case fixture；跨模型接缝用同一历史在指定回合更换 route，比较前后两条，不混入不同用户输入。默认 dry-run 10例；live 必须显式 `--max-calls --max-cost`，费用缺失的候选用最坏上限预留，不能按0元放行；并发默认2，每条2次网络重试上限。断点键 case_hash+route_config_hash+sample_seed，结果已成功不重复调用。

`Route` 字段 `task,base_url,key_ref,model,timeout_sec,max_tokens,fallback_tasks`；key_ref 是配置引用，不把密钥写结果。`resolve_route` 在请求开始解析快照，后续热配置不改变半个回合。超时60秒、回退最多1级；401/403无权错误不自动换用别人的凭据。记录 `route_task,actual_model,attempt,tokens,cost_estimated`，价格映射带查证日期；候选真实id在调用前核对端点，不将历史宣传名当API id。

### 14.3 P1-01/P1-03/P3-01：内容资源与情绪参数

运行时资源 schema=1：`PersonaSlice{id,kind,namespace,trigger_ids,priority,examples[<=3],instruction,source_doc,version}`。核心 identity/boundaries 不接受动态覆盖，正典条目和例句写在 persona 实例资源目录；JSON Schema 校验失败时整个新版不激活，继续旧版本。编译先固定核心，再按状态谓词选至多3条动态切片，总动态预算800 tokens；缓存键 persona_version+state_fingerprint，不跨人格复用。

状态谓词与指纹（2026-09-07 补充，P1-01/P3-01 共用）：`trigger_ids` 引用封闭状态键集合，谓词是 JSON 条件 `{"field","op","value"}` 的 AND 列表，不支持任意表达式或脚本。当前 `state_keys_version=2`：基础键为 `derived_stage`、`trust`、`intimacy`、`energy_band`、`emotion.<name>`（该情绪存在且 intensity≥0.4）、`time_of_day`、`quiet`；P3-01 新增 `attitude.patience/humor/guard/directness/followup`，仅允许对 0–1 数值使用 gte/lte。新增键必须同步 eval 用例。`state_fingerprint` 为上述键按固定顺序取值做 canonical JSON 序列化后的哈希；某切片未引用的键不参与该切片的指纹。`time_of_day` 值域（审查 I9 补）：`morning`（06–12）/`afternoon`（12–18）/`evening`（18–23）/`late`（23–06），按用户时区本地时间；`energy_band` 值域 `high`/`normal`/`low`（由既有 state 精力分档映射）。

情绪集合首版 joy/sadness/anger/hurt/anxiety/calm/tenderness，intensity∈[0,1]，同时最多3条，超量按绝对强度和稳定id排序；不以删除情绪事件原文保持情绪。“吃醋”若后续加入只能是低压角色表达，不产生控制用户的规则。消退 `i(now)=i0*2^(-hours/half_life)`，初版半衰期 joy6h/sadness12h/anger4h/hurt12h/anxiety6h/tenderness8h，calm作为无主态回退；i<0.05移除。显式道歉/安抚按已验证来源加0.2修复量，每来源只一次。

态度基础向量以0–1存储：calm=(patience .6,humor .3,guard .3,directness .5,followup .3)，anger=(.25,.1,.8,.85,.1)，hurt=(.4,.1,.7,.35,.15)，joy=(.75,.7,.2,.6,.5)，sadness=(.4,.1,.45,.4,.15)，anxiety=(.45,.1,.65,.55,.25)，tenderness=(.8,.35,.15,.5,.35)。归一化强度加权后，trust降低guard最多0.2、intimacy提高柔软表达最多0.2；深夜/低精力只能降低followup与长度，不升级亲密。所有轴clamp，固定边界最后覆盖。

测试锚点包括：高信任低亲密不暧昧、被冒犯仍允许用户离开、关心与受伤并存、不相关天气不扣用户信任、两小时一步和两步消退等价。参数由可审JSON表管理，变更表也跑人格eval；本表是可执行初值，不声称已完成人格质量实测。

### 14.4 P1-02：预算、生命周期和引用实现细则

`ContextProvider.collect(user_id,query,state,turn_id)->list[ContextCandidate]`；candidate含immutable id/source_version/text/token_count/source_namespace/required_permission。选择器 `select(candidates,budget=1200)` 保留原系统核心，不把1200当完整prompt总上限；动态区最多4条，稳定排序 priority desc/relevance desc/id asc。首期sticky=2个成功回合、cooldown=4个成功回合，关闭/取消/源删除优先于sticky；普通非命中轮不续sticky。

生命周期 `context_lifecycle(user_id,entry_id,last_committed_turn,sticky_until_turn,cooldown_until_turn)` 放KV或实现既定单表，首期固定采用表便于唯一键/CAS；运行时不导出，reset清理。候选读取无写入，只有已发送并成功记录的turn提交计数。vector阈值沿用当前知识召回量纲，在离线标注集上选“无关聊天零误触发”阈值，不直接照抄别库0.95；阈值落配置及测试快照。（2026-09-07 补充：标注集由 ZCode 构建合成数据，首版 ≥200 条——正例为相关语境命中、负例为无关日常聊天，存 `backend/evals/fixtures/context_labeled.jsonl`，不使用真实私人聊天。）

### 14.5 P1-04/P1-05：日历、行程与前台可及性

`ScheduleBlock(id,weekday,start_local,end_local,location_id,activity_id,presence,energy_delta,mood_delta)`；presence=home/mobile/announced_offline。跨午夜拆两段统一block_id，DST重复小时用UTC period key。日常每天最多一条生活素材、7天内模板不重复；特殊行程需预告标记，离线窗最多2小时，可从聊天直接请求即时回应，不锁输入框、不扣分。

模板选择是种子hash(user_id,local_date,template_version)；实际推进写 `character_life_events(user_id,block_id,occurrence_start,kind,payload_json,namespace='character_fiction',occurred_at,computed_at)`，唯一user/block/occurrence/kind。location来源必须在正典存在。迟到补算保留computed_at，不冒充真实外界观察。每小时能量只结算未处理区间；按块总delta分摊且CAS版本，读时无副作用。天气调制使用已存来源摘要，tick不每次发网络查询。

纪念日预热窗口[-3,-1]天只入选一次，当天一次；普通节日由用户时区日历确定，重复tick不重复事件。季节mood偏移首版±5、能量±3、主动候选priority±0.1，只调基线不直接增发。删除/更改纪念日要撤销旧周期实例；自然季节与关系季节使用不同kind，解释只显示已生效的来源。

### 14.6 P2-02：偏好权威解析与撤销

类别固定 comfort/address/reminder/humor，`value_json`分别用 `{style}`、`{allowed,forbidden,contexts}`、`{intensity:0..2,quiet_hours}`、`{forbidden_topics,allow_teasing}`；解析顺序：用户明确禁令 > 最近已确认教学 > 有效场景偏好 > 核心默认。旧称呼/提醒配置在迁移时变成 origin=legacy 的已确认记录，停止旧路径独立写入，兼容接口转调新服务。

`propose_preference(user,text,source_id)` 返回候选；`confirm_preference(id,expected_version)`激活；`revoke_preference(id)`立即失效并清registry缓存。明确“以后叫我X/别拿X开玩笑”可按用户本轮指令直接确认，疑似推断必须显示草稿。API GET/POST/DELETE `/api/memory/preferences`，更新PUT `/{id}`。负反馈只影响被明确指向的策略，不把普通“嗯/哦”永久写成心理侧写。测试已有记录切换到新resolver前后输出一致、撤销恢复次高优先级而非清掉全部偏好。

### 14.7 P2-03/P2-04/P2-05：有界任务与唯一投递

约定新增 owner=user/assistant、due_at可空、action_kind可空、source_message_id。`extract_promises`先提取owner候选再由结构校验确认，owner=assistant且无可执行action时只成为叙事约定，不后台运行任意工具。相同来源+规范化promise_hash唯一；状态open/done/cancelled/expired，due_at为空不自动失约。promise_hash=按 13.2 固定顺序（NFKC→lower→去标点→压空白）规范化正文后 sha256，owner/due/action_kind 不参与（2026-09-07 补充，审查 I6 定序）。

链实例固定采用表 `event_chains`，不用多个不一致KV：user_id/source_event_id/rule_id/rule_version/node/status/due_at/attempt/result_id，唯一user/source/rule。首条链probability=1（明确约定跟进），depth≤3、attempt≤2、expiry7天。概率后续生活链只在创建时采样一次。visible_delivery记录唯一user/chain/local_date，日上限1；先共享额度认领再表达，失败归还/记冷却按照现有policy语义，不能在这里发明第二份计数。

追发默认20分钟后可用、2小时过期、每来源至多一次；用户新消息或停止可撤销未发实例。晚安不建立24小时惩罚锁，仅取消该会话追发；第二天正常问候仍走原条件。气泡分段最多3段、人工间隔0–800ms、reduced-motion或关闭节奏时为0；同一逻辑消息一个持久化正文，段是呈现事件不是新消息/新好感来源。

### 14.8 G01：重要性计算、冷热分层与视角表

首期 `memory_policy(fact_id,user_id,tier,score,score_version,review_at)` 一条事实一条，tier=short/long；原facts仍权威，原到期硬边界先执行。评分 `S=40*explicit_importance + 20*relationship_anchor + 10*min(distinct_days_mentioned,3)+10*first_event`，布尔取0/1，总分clamp100；pinned单独优先，不通过分数表示。模型confidence不能因score升高改变。

明确“别记住/临时轮”不创建facts或policy；明确“长期记住”使用既有pinned路径；敏感/never_surface无主动召回资格，无论S多高。S≥60进入long，S≤30且未pinned进入short，中间保持原tier，形成滞回；新条目默认short。short默认30天，明显当前状态7天，临时事件1天；用户明确期限优先，已有更早expires_at不可因升long自动延长。稳定长期事实无自动expires_at，但容量清理仍只能清unpinned。

只对新产生或显式重新评估的事实启用，旧条目迁移保持原期限/tier=legacy，避免上线瞬间大规模删除。首次shadow跑14天的**逻辑回放测试时间**，不强制用户等真实14天才能验收；配置启用后真实观察独立记录。score输入存来源id和计数，不复制文本。索引long/short都保留单一fact_id，short按查询相关度临时参与，不建另一份“短期事实库”。

`memory_annotations(user_id,fact_id,role='assistant',emotion,viewpoint,origin,confidence,source_event_id)`与`first_occurrences(user_id,event_type,topic_key,source_event_id)`进入LC-1。topic_key 由提取器返回的 canonical 主题短语做与 promise_hash 相同的规范化生成（2026-09-07 补充）。初历源被删除时删除初历标记且不自动让旧历史第200次吃首次奖励；新的未来事件才可重新明确建立首次记录。

“重要记忆不确定”仅对仍合法存在但verified_at久远/用户给出冲突的事实发出澄清，每事实30天最多一次；自然到期且未显式删除的内容若需提醒，先在到期前发候选，到期后不靠墓碑复原原文。被明确删除的数据绝不留情感纪念残片。

验收输入：explicit=1/anchor=1→60进入long；score29 short、45保持；pinned score0不降级；confidence0.4不变；同日刷三次计1天；用户指定明天过期但score100仍明天过期；源删除SQLite/向量/注释全失效。采用G01既定VERIFY，不再把评分设计留给实现者。

### 14.9 G02/G03/G04：剩余触发条件与存储

G02 `signals={duration_bucket:0..5,edit_bucket:0..3,length_bucket:0..6,pasted:bool}`；前端只持内存累积，发送后重置；粘贴不算“快速输入”信号。开关默认off，开启后后端仍仅在本轮内存消费。长度基线从已有最近10条获授权用户消息即时计算，不另建逐键遥测表；少于5条不用基线，短句仅缩短建议回复10%，不扣任何状态。速度/错字无法可靠判断时不推断。

G03固定新建 `open_questions(user_id,source_message_id,topic,status,next_check_at,expires_at,attempts,last_evidence_hash)`，status=open/researching/resolved/dismissed/expired；7天到期、最多2次复查、最小间隔24h。首次只在明确“以后有结果告诉我/帮我继续查”保存；一般“我不知道”正常回复但不偷偷长期追踪。`research_open_question(id)`使用JOB-1，证据散列未变不发送，evidence_hash=排序后证据条目（canonical_url+title+摘录）的规范化哈希、不含模型叙述（2026-09-07 补充）；成功结果经pending候选与共享额度，源消失立即dismissed。LC-1导出进关系待办类别。

G04 `companion_requests(user_id,life_event_id,kind,status,offered_at,expires_at,response_message_id)`，kind首版song_choice/book_choice，status=candidate/offered/accepted/declined/expired。候选需intimacy≥50且trust≥50，7天至多一次，24h无回复自动expired不再问。用户回复“随便/不想”直接declined；接受后只写角色活动产物，与现实承诺账分离。API GET `/api/companion-requests` 列表、POST `/api/companion-requests/{id}/respond` 应答（2026-09-07 修正原文 GET/respond 笔误），聊天意图也转同一函数；候选不绕过initiative。进入LC-1，删除生活源使未完成请求失效。

### 14.10 P2-01：双门槛关系阶段（用户已确认）

trust/intimacy范围均0–100，旧affection初次回填两维相同。阶段函数固定 `stage_of(min(trust,intimacy))`，使用原25/50/75阈值；羁绊75/85/95同样双门槛。例：90/20=初识、90/55=亲密、80/76=恋人。新meta同时返回两维和derived_stage，旧value/fill兼容字段取min，不再允许它独立写入。

`apply_relationship_event(user_id,event_id,rule_id)->Dimensions`：唯一user/event/rule，事务读取当前两维+version、clamp增量、更新两维并记ledger。首版完成明确约定trust+2；明确尊重边界trust+1；用户自愿真实披露intimacy+1；被明确接纳的角色披露intimacy+2；已确认冒犯trust-2且tension按旧上限；拒绝、离线、短句均0。正向每维日总量≤4，负向每维≥-6；删除引起的纠正按有效ledger重放，不把被删事件继续留为数值来源。事件 reducer 落点（2026-09-07 预研补充）：`core/relationship_events.py:record`（幂等 INSERT OR IGNORE 模式），`apply_relationship_event` 与 L05 领域信任共用此唯一入口。

事件产生架构（2026-09-07 拍板）：确定性钩子与夜间离线提取分流。结构性事件（活动完成、P2-03 约定状态机推进）在既有完成路径上确定性产生 event 并即时入账；对话语义事件（尊重边界、自愿披露、接纳角色披露）由每日批处理按 `extract` 任务键（13.7）提取，次日带源 message id 入 ledger，可撤销/纠正重放。冒犯仅明确确认入账：对话内她明确指出且用户回应、用户道歉、或用户自认过分三者之一；夜间提取只产出高置信候选供确认，不直接扣分。她在当轮的情绪反应（如受伤）由 P1-03 即时表达，与信任入账时机解耦。

迁移按 `NULL` 判首次，不给字段直接 DEFAULT=0后无法区分旧用户；新用户显式初始化原默认值。`set_affection`兼容调试入口在同事务设置两维相同并写manual原因；普通业务禁止调用它替代事件增量。`affection.on_message`里既有首次聊天/昵称等纯频次奖励退出主维度，仅保留互动统计，避免新旧同时涨分。关系变化源可以用户查看高层原因，但不为低值制造压力任务。

### 14.10.1 阶段内小档：好感度的更细划分（2026-09-07 用户拍板）

四阶段门槛（25/50/75）与羁绊（75/85/95，双门槛）不变；**每个阶段内部按该阶段主维（低维）划 3 个小档：早/中/晚**，切点固定在阶段区间内插值——初识/熟悉/亲密为 [0,25)/[25,50)/[50,75) 区间，切点 = 起点 + 区间宽度×1/3 与 ×2/3（初识 8/17、熟悉 33/42、亲密 58/67），恋人与羁绊沿用既有 75/85/95 界。派生函数 `substage_of(trust,intimacy)->(stage, substage)` 只读派生，**小档不新增存储字段**、不写 ledger，两维变化时自然重算。

小档的三层体验差异（用户拍板：三层全要）：
1. **升档专属台词**：每阶段×2 个切点＝8 个升档时刻，复用 unlock 时刻模式（确定性触发＋一句她说的话＋unlock 台账），不占主动额度、当轮自然带出；台词随 P1-01 内容流程补写进侧写档案成长轴。
2. **可见进度感知**：现有「我们之间」/成长展示区显示当前阶段＋小档（如「熟悉 · 中」）与到下一档的大致刻度（三档粒度，不显示精确数值）；L03 关系分支上线后同区展示。
3. **实质内容解锁**：小档只解锁「表达带宽」类内容，不碰边界与安全——晚档比早档多：话题池各 2-3 个新话题（经 P1-02 registry 注入）、称呼候选池扩一档（P2-05C resolver 消费）、日常披露层一条（PS-REV 对应层内细目）；解锁条目由 P1-01 编译器按 `substage` 谓词消费，不新建第二套门控。禁止：小档解锁暧昧/越界内容（阶段边界仍由 stage 控制）、进度压力文案（不显示「还差 X 分」）。

实现落点：`affection.py` 加 `substage_of`（纯函数）与 meta 负载扩展；unlock.py 加升档时刻注册；侧写档案成长轴补 8 条升档台词（P1-01 内容流程）；无新表无迁移。测试：切点边界两侧各一、旧用户迁移后 substage 与原阶段一致、进度展示不泄漏数值。

新表 `relationship_dimension_ledger(user_id,event_id,rule_id,trust_delta,intimacy_delta,occurred_at,reverted_at)`，唯一user/event/rule，进入LC-1关系状态类别；已有affection_log历史保留并标legacy，不虚造原本不存在的二维历史。日期图在迁移点前显示旧值轨迹，之后分维。测试两次迁移不重置90/20、恢复旧包只回填一次、重复事件不二次加分、事件撤销重放不覆盖其他新事件。

## 15. 七项功能反馈的完整实现任务书

### F01：基于真实素材的问候与变体池

**文件/接口**：修改 `core/greeting.py:_greeting_text/greeting_for`、`initiative.py:_build_proactive_prompt`；新建 `core/greeting_material.py`，`collect_greeting_material(user_id,now)->list[SourceRef]`、`choose_greeting_variant(user_id,context)->Variant`；新增 `tests/test_greeting_material.py`。

**数据**：复用关系事件/活动/角色生活事件，不建新原文表；`greeting_variant_usage(user_id,variant_id,last_used_at,last_source_id)`为runtime，reset清理不导出。变体资源放人格切片目录，至少忙碌后/普通归来/完成活动/无素材四类，每类3个方向模板，不预存“你今天做了X”的假事实。

**流程**：沿原gap门控 → 取近30天最多3条授权事件（正在进行活动优先）→ 排除敏感/never_surface/过期/源删 → 同一variant 7天冷却 → 结合state生成最多2句 → OUT-1 → 检查生成期间last_user_turn是否变化 → 原子入选/发送。无素材用普通问候不凑经历；有新用户发言丢弃候选，不占第二次可见额度。

**失败/验收**：模型失败用无事实短回退；源在生成中被删除则丢弃并仅一次无源回退；并发两个窗口只见一条。显式进入页面的问候与自主推送保持各自既有入口，均经已有policy判断，不把前台打开重复算成后台主动。开关off只退素材/变体增强，不破坏原去重。QA-1适用；新增脚本验证无源、同源重复、隐私、迟到候选、角色虚构素材明确标注。

```text
VERIFY: .venv/Scripts/python.exe tests/test_greeting_material.py ;; .venv/Scripts/python.exe tests/test_offline_narrative.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py
```

### F02：日记、约定、研究与惊喜素材互通

**文件/接口**：新建 `core/narrative_sources.py`，`resolve_source(user_id,ref)->SourceMaterial|None`、`collect_sources(user_id,purpose,limit)`；接 `daily.py`、`surprise.py:_pick_material`、P1 registry；测试 `tests/test_narrative_sources.py`。

**数据**：LC-1的source_links，owner_type白名单diary/research/promise/artifact，source_type白名单event/activity/fact/knowledge/character_life。引用方向明确，无限递归禁止；解析最多深2、总5项、1200token。现实/角色虚构/观点三种namespace分别编译，不能把她写的日记当第二独立证据证明同件事。

**步骤**：创建/更新产物时保存来源版本 → 获取下一生成素材先校验源权限与类型 → 日记只汇总当天实际记录，研究可关联知识，约定关联目标但不能凭日记文案自动完成 → 惊喜只拿用户已保存的合法artifact，维持原14天/概率限制 → OUT-1保存结果和引用同事务。只增强素材，不为既有历史推测补链接。

**失败/恢复**：源消失立即拒绝使用，派生缓存失效；已保存产物按删除契约删除/编辑清除来源内容，不保留敏感摘录。批量恢复先按拓扑重映射source_links，自引用第二阶段回填，未识别类型预览报错。关闭provider不删产物。验收读一篇日记不生成新的“真实事件”，源删→所有下游不可再引用，非法namespace不串层。

```text
VERIFY: .venv/Scripts/python.exe tests/test_narrative_sources.py ;; .venv/Scripts/python.exe tests/test_diary.py ;; .venv/Scripts/python.exe tests/test_surprise.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### F03：普通聊天中的心事门控

**接口/文件**：`pending_thoughts.context_candidates(user_id,query,turn_id)`新增，registry注册thought provider；`tests/test_thought_context.py`。复用现有due_thoughts/next_thought_for_stage，不另造第二份心事。

候选必须active、earliest_at≤now<expiry、source合法、stage允许；查询相关性沿现有关键词+经测试阈值的向量，最多1条、200字。用户说“不提这个”走同一dismiss_thought；只在最终回复确实采用该candidate id且成功落库后记录consumed，不能因为注入就mark_expressed。模型无法可靠声明使用时保守不记已表达，但该source按registry冷却4回合，避免每轮重复。

附 `thought_context_receipts(user_id,thought_id,turn_id,status)`，唯一三元组、runtime，记录selected/committed，reset清理、不导出；不写模型全文。临时轮可按现有隐私规则读取，但不写receipt或改变thought状态。删除源使receipt对应候选失效；不消耗后台主动额度，因为它附着用户发起的一轮，但同一心事已表达仍不后台再发。

```text
VERIFY: .venv/Scripts/python.exe tests/test_thought_context.py ;; .venv/Scripts/python.exe tests/test_m5_thoughts.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py
```

### F04：专注收尾人格化与可重试投递

**已有入口** `focus.complete_focus/current_focus/wrapup_eligible/maybe_send_wrapup`；保持自然到点或有效专注≥5分钟规则，不重做计时器。新建 `tests/test_focus_wrapup.py`，修改core/focus与既有API调用点。

`build_wrapup_material(detail,state)`只取真实已用时间、是否中断、用户显式目标、当前行为帧；生成一句≤100字，不评分、不声称用户完成了目标。`wrapup_outbox(user_id,activity_id,status,candidate_text,attempt,next_retry,delivery_id)`唯一user/activity、runtime不导出但reset覆盖；completed事件先写，outbox在同事务插入。

用JOB-1认领，OUT-1校验后调用既有特殊收尾投递通道，**保留不占每日主动额度的已定语义**，但重复投递以delivery_id去重。API超时不回滚已完成的专注；后台重试最多2次，最后用确定性短回顾。取消的活动永不发收尾。关闭增强只退回原文案，不关原计时。

```text
VERIFY: .venv/Scripts/python.exe tests/test_focus_wrapup.py ;; .venv/Scripts/python.exe tests/test_focus.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py
```

### F05：共读方案 C 的阅读地图与书签解锁

**修改** core/activities 的start_reading/set_position/complete_activity/_compile_book_summary、api/activities、ActivityPanel；**新建** `core/reading_map.py`、`tests/test_reading_map.py`。现有kb_chunks只是检索块，不能直接当完整章节结构；L01摄入新增阅读段映射后优先使用，没有则按当前文档文本稳定段落切分。

**模型**：`reading_segments(user_id,activity_id,segment_index,source_start,source_end,source_hash,title,status,completed_at)`唯一user/activity/index；`reading_bookmarks(user_id,segment_id,origin,user_view,tuzhan_view,summary,status,source_version)`唯一user/segment。status地图locked/current/read；书签empty/draft/confirmed。只建空框架，不由LLM通读整本书生成“已经讨论过”的内容。

**API**：GET `/{activity_id}/reading-map`；POST `/{activity_id}/segments/{index}/finish`带expected_version；POST `.../bookmark-draft`仅返回草稿；PUT `.../bookmark`保存用户确认。兼容旧position接口只导航，不把跳到末页视为全部已读；finish是唯一解锁事件。

**流程**：开书事务一次建所有segment → 当前段完成时标read并开放当前bookmark编辑 → 摘录只取source_range与真实用户/角色已确认观点 → OUT-1草稿→用户确认 → 读完全书时确定性汇总confirmed书签，未填部分显示省略计数，不替用户补观点。用户可显式提前结束，产物注明只含已读段。

**迁移/删除**：旧活动按当前位置以前的段标legacy_position（可导航但不捏造确认书签）；保留旧activity_notes，映射至对应段，不能自动把备注当共同结论。LC-1活动类别，源文档改版hash不符返回409要求新建阅读版本，不悄悄重定位。文档删除调用activities既有forget路径清两表/相关artifact引用；临时轮不建段。

**验收**：重开同书不重复地图；跳页不提前解锁；两客户端完成同段幂等；模型观点不进user_view；生成中改段/删书结果失效；全书汇总只含确认项；恢复后source_range与hash一致。feature关闭保留旧活动读取，新的地图只不展示。

```text
VERIFY: .venv/Scripts/python.exe tests/test_reading_map.py ;; .venv/Scripts/python.exe tests/test_activities.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py ;; npm --prefix frontend test
```

### F06：聊天意图自动预填，用户确认立项

**新建** `core/activity_drafts.py`、`api/activity_drafts.py`、`tests/test_activity_drafts.py`；**修改** intent/pipeline/ChatView，复用goals.start_goal/cowriting.start_writing/colists.start_list。采用独立可见草稿事件，不把创建工具藏进普通回复。草稿卡片呈现为独立前端切片后置（2026-09-07 拍板）：本片先以现有消息事件暴露草稿引用与确认端点，卡片化交互另行立项。

`ActivityDraft{draft_id,source_turn_id,kind,title,payload,persona_id,expires_at,source_version}`；kind goal/writing/song_list/book_list，payload用各既有创建API的字段白名单。服务端无持久draft表：返回经签名的短期草稿引用或存在会话内存20分钟，重启过期可重新生成；不进入关系导出。

用户明确“想一起做X”产草稿，普通提及“昨天读了一本书”不产；置信不足只问一句所缺主题不创建。前端可改title/payload然后POST `/api/activity-drafts/confirm`，服务端重新校验user/epoch/expiry及活动互斥，调用权威start函数，幂等键user/draft_id记录已创建activity_id（小型runtime receipt，reset清除）。同draft二次确认返回同activity。

已有活动冲突按原壳暂停策略先在卡片告知，确认后一个事务创建/暂停，不私自丢失计时。用户取消草稿不影响活动；临时轮不产生可持久确认句柄，明确提示本轮不创建。回滚关draft输出，原手动入口保留。验收未确认零DB增量、伪造kind/过期/跨人格拒绝、生成失败不自动用猜测立项。

```text
VERIFY: .venv/Scripts/python.exe tests/test_activity_drafts.py ;; .venv/Scripts/python.exe tests/test_goals.py ;; .venv/Scripts/python.exe tests/test_cowriting.py ;; .venv/Scripts/python.exe tests/test_colists.py ;; npm --prefix frontend test
```

### F07：对话内的记忆生命周期露出

**修改** explainability.build_reply_explanation、api/memory_admin、MessageBubble/MemoryPanel；新建 `tests/test_memory_lifecycle_display.py`及组件测试。只给本轮已实际使用且允许展示的fact id附`{pinned,expires_at,confidence,verified_at,can_edit}`，不另召回更多事实。

后端在返回时二次授权，用户气泡旁按需折叠显示“保留到X/长期保留/尚待确认”；客户端不展示原始权重算法。固定/纠正/删除点击复用权威API并带version，成功刷新这条解释，410/404显示已删除并移除全文，不缓存旧敏感正文。历史解释只是过去来源快照，不能绕过当前删除权限重新取文。

无新表，解释JSON增加可选键，旧客户端忽略、旧记录没有时不渲染。临时轮仅有当轮状态标记，不落解释。验收仅标已引用条目、时区日期正确、取消固定不凭空延长原expires_at、删除后历史气泡无可重新曝光的全文；keyboard/读屏状态可用。

```text
VERIFY: .venv/Scripts/python.exe tests/test_memory_lifecycle_display.py ;; .venv/Scripts/python.exe tests/test_memory_correction.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py ;; npm --prefix frontend test
```

## 16. TECH-PLAN 中此前遗漏或仅标延期的路线

### L01：网页正文、EPUB 与结构化摄入

**文件**：扩 `core/knowledge.py:detect_format/parse_document/ingest_document/chunk_text`、api/knowledge；新建 `core/document_import.py`、`tests/test_document_import.py`。前端复用书架导入入口。接口POST `/api/knowledge/import-url {url,request_id}`及原文件上传支持.epub；同步校验后大文件返回202 job_id，可取消，解析在worker不阻塞聊天。

**数据**：kb_documents新增format/source_url/source_hash/parser_version；`document_segments(user_id,document_id,index,title,text_start,text_end,content_hash)`独立于检索chunks。源文字与原文件归所属人格目录；网页记录抓取时间和canonical来源，初版不执行JavaScript，不绕登录/验证码。EPUB按OPF spine顺序提正文与章节，不按zip文件名字母顺序。

**流程**：上传≤20MB/解包≤100MB/条目≤2000/文本≤200万字符 → 魔数与声明双验 → 受限解析 → normalize换行并保留段落映射 → 按结构选择factual/narrative/quote切分（列表/表格行不断，叙事滑窗与段落对齐）→ SQLite写段与chunks → 向量异步索引。不能为兼容EPUB破坏既有txt/md/pdf解析。

**边界**：URL只http/https，禁loopback/private/link-local/云metadata与解析后重定向绕行，DNS每跳校验且实际连接目的地固定；超时15秒、最多3跳。EPUB禁绝对路径/../、外部实体、脚本、自动加载远程媒体及解压炸弹，文件只写沙箱输出目录。DRM/损坏文件返回可懂错误，不盲取垃圾文本。

**生命周期/恢复**：LC-1知识库类别；同源hash重复返回已有doc，版本变更创建新版不覆盖在读映射。取消清partial不删已有书；失败库事务回滚，索引失败标pending可重建。源删清阅读段与F05地图关联。导出明确含源文件与解析文本或仅元数据，缺源包不可伪称可续读。QA-1+恶意URL/zip测试，模式关只禁新格式导入。

```text
VERIFY: .venv/Scripts/python.exe tests/test_document_import.py ;; .venv/Scripts/python.exe tests/test_knowledge_base.py ;; .venv/Scripts/python.exe tests/test_activities.py
```

### L02：世界观、角色设定共创和观察日志

**文件**：cowriting增加subtype=story/world/character；新建 `core/observations.py`、`api/observations.py`、`tests/test_creative_extensions.py`。在现有ActivityPanel切换种类，不另建创作面板。

world/character仍用writing壳和writing_turns，新增`activity_writings.subtype`默认story、`structured_outline_json`，字段白名单world={locations,rules,timeline}、character={name,traits,relationships}。`propose_outline(activity_id)`返回草稿，`confirm_outline(id,version)`由用户确认；创作内容source_namespace=fiction，不能自动写入她的核心正典。明确“作为角色设定参考”也只进入可撤销实例覆盖，需P1资源审核。

观察日志复用activities(kind='observation')，侧表`observation_entries(user_id,activity_id,observed_at,observer,content,source_type/source_id,confidence)`，observer=user/assistant；用户所见是user_record，角色生活所见character_fiction，模型推断inference。接口start/add/pause/resume/complete/cancel/export与既有活动语义一致，单人格活跃壳互斥。无真实观察不允许LLM补成纪实日志。

完成world/character生成版本化co_world/co_character artifact，观察日志为observation_log并保留条目来源。新artifact类型进入白名单/虚构排除器，双视角/快照不能误收fiction事实。LC-1活动类别，源删与活动取消按壳清理；生成超时保留用户原稿，重试不重复turn。验收各生命周期、不同subtype、重复完成同版hash不增版、恢复引用重映射。feature关只停止创建，旧记录可看/导出/删。

```text
VERIFY: .venv/Scripts/python.exe tests/test_creative_extensions.py ;; .venv/Scripts/python.exe tests/test_cowriting.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### L03：关系分支与可解释形成原因

用户已选可查看高层描述，不显示进度条/等级。新建 `core/relationship_style.py`、`tests/test_relationship_style.py`，接seasons/behavior与现有“我们之间”或解释入口；GET `/api/memory/relationship-style`、DELETE清除自动偏好。

**模型/算法**：`relationship_style_evidence(user_id,event_id,style,weight,occurred_at)`唯一user/event/style；style=companion/playful/confidant/growth/romantic。只用明确事件：共同活动→growth、明确互相理解→confidant、用户确认喜欢的梗→playful，romantic额外受双维阶段约束。窗口90天，权重按30天半衰期，仅影响气质不扣关系分；至少3个不同日期有效事件才显示总结，否则“还在慢慢形成”。最高两类权重差<10%可并存，不强行唯一标签。

`derive_style(user,now)`返回style_ids、最多2条来源简述、valid_until，不向UI返回分数。生成文案由固定标签+来源短句或OUT-1，不写“解锁X关系”。自动倾向对behavior单轴最大±0.1，不能覆盖显式用户偏好和阶段边界。

LC-1长期关系类别；reset删除证据，源删后即时重算；手工“不要这种气质”走P2偏好屏蔽。保持旧seasons（当前时期）与style（长期气质）分离。验收一个高频单日事件不能形成分支、离线不触发惩罚、删除关键源后解释更新、低亲密没有romantic。

```text
VERIFY: .venv/Scripts/python.exe tests/test_relationship_style.py ;; .venv/Scripts/python.exe tests/test_m4_relationship.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### L04：幽默记忆、共同语境与梗的退休

**复用** user_terms、user_style_map与 `daily.extract_terms`；新建 `core/humor_memory.py`、`tests/test_humor_memory.py`。侧表`humor_usage(user_id,term_id,source_turn_id,reaction,last_used_at,blocked_until,status)`，status=candidate/approved/retired；positive/negative/unknown仅接受明确反馈，“哈哈”单条只unknown，避免把客套当长期授权。

`record_humor_feedback(term_id,turn_id,reaction)`幂等；`select_humor(query,state)`先排禁区/初识不适用/retired，近7天至少2次明确positive才approved，最近3个回合不重复；明确“别再玩这个梗”立即retired不等负票累积。单次最多一梗，严肃问题/求助/修复时默认不插。

用户可在现有共同语言记录上删除/禁用，delete_term同步侧表与registry缓存；导出保留有效偏好，不导出runtime逐次用量明细；LC-1迁移。复用source引用，不保存额外完整聊天。模型建议的梗只是候选，不自行改写人格。验收边界负反馈优先于历史positive、源删不能再讲、重启冷却一致、同一天重复表态不刷批准。

```text
VERIFY: .venv/Scripts/python.exe tests/test_humor_memory.py ;; .venv/Scripts/python.exe tests/test_shared_terms.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py
```

### L05：领域信任，不替代二维关系

**新增** `core/domain_trust.py`、`tests/test_domain_trust.py`；接P2事件ledger/behavior/偏好resolver。表`domain_trust_events(user_id,event_id,domain,delta,rule_version)`唯一user/event/domain，domain=emotional/task/privacy/promise/humor；`domain_trust_snapshot(user_id,domain,value,version)`派生快照。

首次领域value=全局trust而不是0；明确任务兑现task+2、承诺兑现promise+2、尊重边界privacy/humor+1，明确已证实的违背对应-2，日每域±4、0–100。领域事件不再加一次全局trust：同一事件由唯一relationship reducer同时算global/domain增量，禁止双入口叠加。

领域只改变相应披露/求助/玩笑强度，不能因task低拒绝基本聊天、不能因privacy高绕过隐私开关；双维阶段仍取min(global)。GET `/api/memory/domain-trust`返回高层可依赖程度与最多2条来源，DELETE reset按当前global重建。LC-1；撤销事件重算指定域，离线不自动降分。测试domain影响不串轴、重复事件一次、来源删除重放、匿名/其他人格不可读。

```text
VERIFY: .venv/Scripts/python.exe tests/test_domain_trust.py ;; .venv/Scripts/python.exe tests/test_relationship_dimensions.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

### L06：低频生活模板池与精力有限选择

**新增** `core/life_templates.py`、`tests/test_life_templates.py`，接schedule/character_life_events/state与surprise；资源每人格JSON schema=1，`{id,location_id,prerequisites,energy_cost,weight,output_kind,cooldown_days}`，首版6个生活模板，均来自正典。

候选每天最多1个、每模板7天冷却、角色精力<30只在rest/quiet_reading内选；>=30才允许outdoor/research，扣能量只在事件提交时一次。概率用用户/日期固定种子，首次没选中当日不能反复抽；触发概率初值0.25，不是保证四天一次。`choose_life_event(user,now)->candidate|None`，无合法模板保持沉默不编。

生活模板只产生character_fiction事件；和已发生真实天气融合要保留天气source id，不把虚构淋雨说成用户所处事实。前端已有聊天内状态一行显示可见变化，详细故事经OUT-1/主动额度。新模板无效fallback rest；用户不回应不追加追问。数据复用P1-04表/LC-1，不另建第二生活历史。验收低能量不会出现高能外出、同日tick同结果、停用模板不抹掉历史但不再引用失效正典。

```text
VERIFY: .venv/Scripts/python.exe tests/test_life_templates.py ;; .venv/Scripts/python.exe tests/test_time_tick.py ;; .venv/Scripts/python.exe tests/test_state_interaction.py
```

### L07：共同审美与房间/数字花园扩展

**文件**：新建 `core/aesthetic_preferences.py`、`tests/test_aesthetic_preferences.py`，接imagegen/CornerPanel/artifacts。首版继续现有CSS/2D展示，不要求新增美术或引入3D引擎。

表`aesthetic_preferences(user_id,owner,category,value,origin,source_id,status)`，owner=user/assistant，category=color/style/motif/layout；用户明确偏好优先，assistant偏爱来源是已确认作品/角色意见，不能通过共同喜欢把她的偏好反写成用户事实。GET/PUT/DELETE `/api/memory/aesthetics`，展示/撤销在既有了解她/设置区。

`resolve_aesthetics(user,purpose,explicit_request)`优先本轮明确要求，其次用户偏好，其次角色偏好；冲突保留不同意见但生图遵守用户本轮要求。选最多3个视觉token进入提示，每token关联source id，禁把外部作品文字当图像工具执行指令。

`artifact_placements(user_id,artifact_id,slot,x,y,theme_version)`只保存布局，物件来自真实artifact。植物/星图等新物件由明确已完成事件生成`relationship_object` artifact，元数据记录source_event，空房间不长假物件；物件成长用真实活动次数不是连续登录压力。用户可隐藏/重排不删源，删除artifact同时删placement。LC-1导出preferences/placements并重映射artifact；无效资产fallback CSS几何图形。验收无源物件不显示、两个owner分开、无障碍/窄屏、恢复位置不越界。

```text
VERIFY: .venv/Scripts/python.exe tests/test_aesthetic_preferences.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py ;; npm --prefix frontend test
```

### L08：工具臃肿治理与统一 VisualState/Presence

**前置桌面宠物必须先完成本片**。新建 `frontend/src/state/visualState.ts`、`core/presence.py`、`tests/test_presence.py`，修改App/ToolBar/Portrait/ChatView及api/meta。不新建另一套后端状态源。2026-09-07 拍板：拆 A/B 两片——A 后端 `core/presence.py` 与 meta 输出统一先行；B 前端 visualState store、工具入口搬移与「更多」弹层为独立呈现切片后置，各自验收。

`VisualState{persona_id,revision,mood_label,bond_label,energy_band,activity_kind,presence,quiet,reduced_motion,source_time}`，只由meta/活动/显示设置解析；不让每个组件各请求并自己解释“忙碌”。前端一个store、请求去重、版本单调；旧响应不得覆盖新persona，退出订阅彻底清理。

工具入口保留聊天输入、语音、活动、更多；其他已存在入口搬入可搜索“更多”弹层/已有设置分组，原功能深链和快捷键保留，最近使用仅本机运行偏好，不追踪聊天内容。不是删除工具：验收逐一按钮功能仍可达，不把调试字段放在用户主流程。

界面简化补充项（2026-09-07 用户拍板，与 L08 同批实施；首屏现状 25+ 元素、header 12 图标、每消息 3-4 附加按钮）：
1. **好感度条移出聊天**：aff-bar 不再常驻 ChatView 上方，数值/阶段/小档进度只在成长页（DashboardPanel）与「我们之间」展示；聊天过程中不可见任何关系数值（拟真总则的 UI 落地）。
2. **状态灯异常才显示**：ToolBar 的 7 个状态 chip（联网/天气/生图/识图/记忆/MCP/在线）正常时整条隐藏，任一异常亮红提示并 clickable 查看详情；全绿=无条带。
3. **不留痕开关收纳**：输入框底部「本轮不留痕」开关收进 ChatInput 快捷指令面板，底行只留状态文字。
4. **解释快照保留**：「为什么」按钮已按需展开，维持现状。
验收：收纳后首屏可见元素 ≤12（header 4 入口＋消息流＋输入区）；全部功能可达性回归照旧（L08 验收条款适用）；好感度条移出后 Dashboard 补展示位。

presence home/mobile/announced_offline/rest/focus来自state/schedule；安静=系统reduced-motion或用户关闭动效或focus，优先压制动画和自动声线。后端只返回可解释状态，不泄漏全prompt。无新业务表，显示偏好写既有persona settings；同步导出该设置入口已有覆盖。feature关闭恢复原工具布局，VisualState仍可作为无行为改变适配层保留。

```text
VERIFY: .venv/Scripts/python.exe tests/test_presence.py ;; npm --prefix frontend test ;; npm --prefix frontend run test:e2e
```

### L09：本地 Whisper STT（实施延期，方案完整）

**新建** `frontend/electron/stt.ts`、`backend/local_stt/worker.py`、`frontend/src/utils/stt.ts`、`tests/test_stt_worker.py`；修改preload/ChatInput。原音频只在本机专用worker内，不传给现有HTTP后端或模型云端；PWA版本该按钮禁用并说明本地桌面能力。

worker提供本地stdin/stdout framed协议：`start{request_id,language,model_ref}`、音频二进制帧、`stop/cancel`，响应partial/final/error；每帧有长度上限，单段≤60秒/10MB、16kHz单声道，非PCM先在worker转码。Electron主进程启动隐藏worker，固定可执行路径/模型清单，renderer不能传shell命令或任意模型文件路径。

模型默认可选small/base本地包，首次用户明确选择下载才取，下载校验和、离线不联网；硬件不足回退base或CPU并显示本地状态，不改走远程。录音仅用户按下开始，权限失败不创建空会话；cancel/切人格/关窗立即停止麦克风与worker请求，原音频RAM清理，必要临时转码文件在受控随机目录finally删除、不进备份。

最终转写只回填输入框并标草稿，**用户确认发送**才调用正常聊天；不能将识别结果直接喂意图工具执行。临时模式也可转写，但不保存音频/草稿统计。测试假音频worker、超限/取消/无模型/麦克风拒绝/切persona迟到结果丢弃；真实准确率用用户提供的非敏感测试语料单独记录。关开关恢复原输入组件。

```text
VERIFY: .venv/Scripts/python.exe tests/test_stt_worker.py ;; npm --prefix frontend test
```

### L10：桌面宠物（实施延期，依赖 L08）

**文件**：新增 `frontend/electron/petWindow.ts`、`displayAvoidance.ts`、`frontend/src/components/PetView.vue`、对应Vitest；修改main/preload、已有settings。使用现有立绘资产与VisualState，不把人格数据复制进Electron本地库。

创建独立透明frameless BrowserWindow，contextIsolation=true/nodeIntegration=false、固定本地页面，IPC白名单仅drag/close/toggleIgnoreMouse。默认不全屏置顶、不自动抢焦点；透明区点击穿透，交互区可拖动，Escape/托盘均可关闭。记录display_id、DIP坐标、scale_factor；屏幕拔插/旋转/DPI变化时用screen.workArea重新clamp，不能按物理像素重复放大。[Electron 窗口](https://www.electronjs.org/docs/latest/api/browser-window)、[屏幕接口](https://www.electronjs.org/docs/latest/api/screen)是实现参考，实际版本以仓库Electron类型声明验证。

全屏避让不能只监听宠物自己的enter-full-screen。Windows小型本地helper读取前台窗口与所在monitor边界（不抓屏、不读内容），命中全屏或用户排除进程时隐藏宠物；轮询1秒，空闲时低频，不安装键鼠hook。helper只返回bool/display_id；失效时保守隐藏。用户切出全屏再恢复，不改变原窗口焦点。

所有动画订阅在close时dispose，贴图释放；最多一个petWindow，快速切换10次不残留timer。位置保存在persona settings的display preferences，跨机恢复时按可用monitor重定位。开关off立即close；退出应用终止本实例helper，不杀其他进程。验收双屏负坐标、125%/150%DPI、全屏播放器、显示器断开、reduced-motion、键盘关闭、8小时稳定性：预热后取同场景内存，增长超20%或50MB进入调查，不宣称短测等于8小时通过。

```text
VERIFY: npm --prefix frontend test ;; npm --prefix frontend run build
```

真机多屏/8小时另写运行脚本和记录，不塞600秒worker VERIFY；界面任务不具备硬件时可以完成mock部分，但未通过真机验收不能打整体完成。

### L11：世界感知来源层（天气、节日、RSS/主题订阅）

**文件与接口**：新建 `backend/core/world_sources.py`、`backend/core/topic_subscriptions.py`、`backend/api/world.py`、`tests/test_world_sources.py`；接入 `daily.py`、`greeting.py`、`initiative.py` 和 P1-02 registry。提供 `refresh_source(source_id, now) -> SourceSnapshot`、`list_candidates(user_id, now)`、`POST /api/world/subscriptions` 与删除接口。天气按用户主动设置的粗粒度城市查询；节日采用版本化日历数据；RSS 只读用户明确订阅的 URL。不得从 IP、设备名或聊天片段偷偷推断位置。

**数据与调用链**：`world_subscriptions(id,user_id,kind,locator,label,enabled,created_at,version)`；`world_snapshots(id,subscription_id,fetched_at,expires_at,content_json,source_url,content_hash,status)`。天气 TTL 30 分钟、RSS 15 分钟、节日数据按版本更新；相同 content_hash 不生成新候选。刷新由 JOB-1 执行，解析后只产生 `context_registry` 的短期候选，未经用户确认不写 facts、relationship_events 或她的经历。问候/主动消息取候选时仍经过 OUT-1 与统一主动额度。

**安全与失败**：RSS 抓取继承 L01 的 SSRF、重定向、大小、超时和内容不可信边界；HTML 只提正文/标题/日期，脚本和远程资源不执行。来源异常保留最后一份未过期快照并标 stale；过期后明确“暂时取不到”，不能把旧天气当今天。删除订阅级联删除快照、registry 激活和候选。导出只含订阅设置，缓存默认不入关系包；临时轮不能创建订阅。

**验收**：冻结时钟覆盖 TTL、去重、过期、来源失败、位置未授权、恶意 RSS、删除后无幽灵候选；主动候选在用户刚发言、额度已满或 source_version 变化时失效。

```text
VERIFY: .venv/Scripts/python.exe tests/test_world_sources.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py ;; .venv/Scripts/python.exe tests/test_context_registry.py
```

### L12：D8 公网 HTTPS、Access、PWA 与 Web Push（实施需域名条件，方案完整）

**拓扑**：新增独立 `gateway` 进程/监听端口，只把该端口交给 Cloudflare Tunnel；现有开发端口 `8801` 继续绑定 loopback，不允许直接暴露。Tunnel 入口先过 Cloudflare Access，源站再次校验 Access JWT 的签名、`aud`、`iss`、`exp`，再校验应用自己的短期 session。Cloudflare 文档明确要求源站验证令牌；不能因为 Tunnel 到源站的 peer address 是 loopback，就沿用 `backend/app.py` 当前“loopback 免认证”分支。[Cloudflare Access 自托管应用](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-apps/) 与 [保护源站](https://developers.cloudflare.com/fundamentals/security/protect-your-origin-server/)是实施基线。

**文件与接口**：新建 `backend/gateway/app.py`、`backend/gateway/access_auth.py`、`backend/core/devices.py`、`backend/api/devices.py`、`frontend/src/stores/devices.ts`、`tests/test_gateway_auth.py`；修改 app 路由分组、CORS/CSRF、SSE、WebSocket（若实际启用）、`frontend/public/sw.js` 和 PWA 设置页。所有 `/api/**`、静态私有附件、SSE 及推送订阅接口共用强制认证依赖；健康检查仅返回无敏感状态。Access 服务令牌/公钥缓存不写日志，JWKS 轮换失败时只允许未过期缓存短暂续用，超窗 fail closed。

**数据模型**：`devices(id,user_id,display_name,platform,created_at,last_seen_at,revoked_at,version)`；`push_subscriptions(id,device_id,endpoint_hash,endpoint_cipher,p256dh_cipher,auth_cipher,created_at,expires_at,last_success_at,failure_count,revoked_at)`；`auth_sessions(id,device_id,token_hash,expires_at,revoked_at)`。endpoint 和密钥材料按 P3-04 加密；VAPID 私钥只在本机密钥仓，不入数据库/备份包。`POST /api/devices/register`、`GET /api/devices`、`POST /api/devices/{id}/revoke`、`POST/DELETE /api/push/subscriptions` 都要求 Access 身份与应用 session 同时匹配 user_id。

**推送链路**：用户在已认证页面明确允许通知后注册 service worker 与 PushSubscription；后端发送的 payload 只含 opaque event id 和通用提示，service worker 点击后再经认证拉正文。Push API 依赖安全上下文和 service worker，且订阅可变化，前端每次启动要对账而不是假定永久有效。[MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)作为浏览器行为参考。401/403 时 service worker 不缓存登录页为 API 数据；现有 `/api`、SSE、人格私有资源继续禁止 Cache Storage。推送 404/410 立即撤销订阅，连续失败退避，不重复发送主动事件。

**上线与回滚**：先本机 mock JWT，再 staging 域名；上线检查 DNS/Tunnel/Access policy、origin 不可公网直连、TLS、CSRF、登出/撤销、多设备和恢复。flag `remote_gateway_enabled` 默认 false；关闭后停止公网监听与新推送，保留设备列表供删除/导出。真正上线需要用户提供域名与 Cloudflare 账户配置，这只是外部条件，不是留给 ZCode 的架构决策。

```text
VERIFY: .venv/Scripts/python.exe tests/test_gateway_auth.py ;; .venv/Scripts/python.exe tests/test_devices.py ;; npm --prefix frontend test ;; npm --prefix frontend run build
VERIFY_TIMEOUT: 600
```

### L13：跨端消息总线与 QQ 官方机器人首接

**渠道选择**：QQ 首期采用腾讯官方机器人 SDK/API，原因是授权接口和凭据边界清楚，通常比注入普通 QQ 客户端的本地桥接风控风险小；这不等于零风险，也不保证具备普通好友账号的全部私聊/主动消息能力。能力以申请到的场景和官方端点实测为准。[腾讯官方 botpy SDK](https://github.com/tencent-connect/botpy)是实现入口。普通 QQ 账号桥接只保留 adapter 接口与风险说明，不进入首期生产配置。

**文件/接口**：新建 `backend/channels/base.py`、`identity.py`、`inbox.py`、`outbox.py`、`qq_official.py`、`tests/test_channel_bus.py`。`ChannelAdapter` 固定 `capabilities() / verify_webhook() / normalize_inbound() / send(outbound) / health()`；统一 `InboundMessage(channel,external_account_id,external_message_id,text,attachments,received_at)` 与 `OutboundMessage(logical_message_id,channel,binding_id,text,reply_to)`。QQ webhook 验签和回执后写 inbox，再由渠道身份绑定解析 user/persona，调用正常 chat pipeline；禁止复用 `/api/remote/task` 让请求体自报 user_id。

**身份与消息数据**：`channel_bindings(id,user_id,persona_id,channel,external_account_hash,status,verified_at,revoked_at,version)`；绑定通过桌面端生成一次性 6 位码（10 分钟、最多5次），用户从对应 QQ 会话发送后完成，不能仅凭昵称/openid 猜身份。`channel_inbox(channel,external_message_id,binding_id,payload_cipher,received_at,status,error)` 以 channel+external_message_id 唯一；`channel_outbox(logical_message_id,channel,binding_id,payload_cipher,status,attempts,next_retry_at,provider_message_id,last_error)` 唯一。正文按 P3-04 加密；保留策略跟正常消息一致。

**一致性**：当前 `sessions.db`、`bot.db` 和 `agent_tasks.db` 不能组成原子事务，因此采用 inbox/outbox 状态机：inbox 认领→调用 pipeline 产生一个 logical_message_id→会话持久化成功→outbox 投递。投递超时为 `unknown`，先按 provider idempotency/查询能力确认，不能盲发第二条；无查询能力则进入人工可见失败状态。跨端历史仍以 `backend/session/store.py` 为权威时间线，渠道表只保存传输状态，不另造聊天真相。

**边界**：附件先做文本与受限图片，复用 vision 事实层；群聊、语音、文件和主动私聊要等 `capabilities()` 明示支持再启用。所有入站内容是不可信用户输入，不能携带 system/tool 权限。撤销绑定后拒绝新入站并停止 outbox；处理中撤销通过 source_version 阻止迟到投递。凭据来自 secrets provider，不进配置模板明文。

```text
VERIFY: .venv/Scripts/python.exe tests/test_channel_bus.py ;; .venv/Scripts/python.exe tests/test_channel_identity.py ;; .venv/Scripts/python.exe tests/test_pipeline_scenario.py
```

### L14：个人微信本地桥接（第二渠道，用户已确认）

**选型与隔离**：首个参考实现采用独立本机 sidecar 适配 WeChatFerry，只把最小消息协议暴露给主后端。该项目文档提供登录检查、收消息与发文本接口，但兼容特定微信客户端版本；每次启动必须做版本与能力握手，失败就暂停渠道并提示用户手工处理，不能自动降级成未知 hook。[WeChatFerry 项目](https://github.com/lich0821/WeChatFerry)与[客户端 API](https://wechatferry.readthedocs.io/zh/latest/autoapi/wcferry/client/index.html)仅作为适配依据，不代表平台官方支持或永久兼容。

**文件/协议**：新建 `bridge/wechat_sidecar/`、`backend/channels/wechat_local.py`、`tests/test_wechat_bridge.py`。sidecar 仅监听命名管道或 loopback 随机端口，启动握手返回 `bridge_version,client_version,logged_in,capabilities`；主进程传一次性 token，双向帧限制 1MB。复用 L13 的 normalize/inbox/outbox/binding，不让桥接进程直连数据库、模型或工具。首期仅一对一文本和图片；群聊、撤回、语音、朋友圈都关闭。

**登录、去重与恢复**：登录必须由用户在本机客户端完成；`is_login=false` 时暂停消费并保留有界 outbox，不模拟扫码、不绕过验证。入站用微信消息 id；缺稳定 id 时以 sender+timestamp bucket+content hash 做短期去重并记录低置信，不能据此永久合并。断线指数退避，恢复后从 bridge 可提供的游标补收；无法补收时明确显示间隙。发送超时进入 unknown，与 L13 同样不盲重试。

**安全与退出**：bridge 二进制/依赖锁版本与校验和，升级单独评审；仅允许已绑定联系人，群事件默认丢弃并计数。日志脱敏 wxid、正文和媒体路径；媒体落加密临时区并按消息保留策略清理。退出主应用停止 sidecar；进程异常超过3次熔断，用户手工恢复。测试用 fake bridge，不在自动测试登录真实微信。

```text
VERIFY: .venv/Scripts/python.exe tests/test_wechat_bridge.py ;; .venv/Scripts/python.exe tests/test_channel_bus.py ;; .venv/Scripts/python.exe tests/test_channel_identity.py
```

### L15：多角色群聊与各自生活（实施延期，方案完整）

**模型与文件**：依赖 persona 隔离、L13 时间线和 P1-04 行程。新建 `backend/core/rooms.py`、`room_arbiter.py`、`backend/api/rooms.py`、`tests/test_multi_persona_rooms.py`；前端在现有聊天页加房间选择，不加独立控制台。`rooms(id,owner_user_id,title,status,created_at,version)`、`room_members(room_id,persona_id,role,joined_at,left_at)`、`room_messages(id,room_id,sender_kind,sender_id,logical_message_id,text_cipher,created_at)`、`room_turns(room_id,trigger_message_id,state,selected_personas_json,expires_at)`。

**隔离与调用链**：角色模板是只读设定，成员实例持有各自 user scope、情绪、行程和关系；默认不能读取其他角色与用户的一对一历史。只有房间内显式消息及用户明确分享的 artifact 进入共享上下文，来源包装标明 speaker。用户消息→确定性 arbiter 按@点名、可及状态、轮次冷却和相关度选最多1名，确有互补价值时最多2名→每个角色独立编译 prompt→按固定顺序提交→OUT-1→统一时间线。模型不能自行无限邀请下一位模型说话。

**行为边界**：角色外出/睡眠时可以延迟或不回应，但不得阻止用户继续聊天；不回应不伪造已读。机器人之间最多一轮回应，随后必须等用户新输入。移除成员后取消其未发送产物；删除房间级联共享副本，不删除角色原有私有资料。导出按房间单独选择；恢复找不到 persona 时保留只读占位，不把内容归给其他角色。

```text
VERIFY: .venv/Scripts/python.exe tests/test_multi_persona_rooms.py ;; .venv/Scripts/python.exe tests/test_persona_isolation.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py
```

### L16：可选择共享知识与创作导出

**范围**：新建 `backend/core/shared_resources.py`、`backend/api/shared_resources.py`、`tests/test_shared_resources.py`，复用 artifacts、knowledge、writing 与 source_links。`shared_resources(id,owner_scope,resource_type,resource_id,namespace,created_at,revoked_at,version)`、`resource_grants(resource_id,grantee_scope,permission,created_at,revoked_at)`；permission 首期只读。默认所有知识、记忆、日记和创作都隔离，只有用户在已有详情入口明确“分享给某角色/房间”才创建 grant。

读取时同时校验资源仍存在、owner 未撤销、grantee 当前成员关系和版本；context_registry 只拿授权片段。创作导出声明 `user_fact / assistant_fiction / shared_fiction / source_excerpt` 命名空间并保留作者/来源，不能把角色虚构当用户事实。撤销立即使缓存与异步候选失效；导出包含 ACL 清单，导入时默认全部收紧为私有，待用户重新授权。共享不复制原文；源删除通过 LC-1 级联。

```text
VERIFY: .venv/Scripts/python.exe tests/test_shared_resources.py ;; .venv/Scripts/python.exe tests/test_persona_isolation.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
```

## 17. M9 外部机制借鉴的可执行补充

### 17.1 表达必要性与注意力漂移

新建 `backend/core/expression_policy.py`、`attention_state.py`、`tests/test_expression_policy.py`。表达评分只用于主动候选和可省略的装饰性句子：`necessity = relevance + novelty + relationship_value - repetition - interruption_cost`，各项0..1、规则版本化；用户发起的一对一消息永远有答复，不能被评分门控为沉默。主动候选默认 necessity≥0.6 才进入现有 arbiter，阈值由离线样例校准，不在线自调。

注意力只保存 `topic_id,weight,last_seen_turn,source_version`，最多5个主题；每轮当前主题+0.35、其余×0.7，低于0.1删除。显式转题立即置顶，活动/心事可提供低权重候选但不能劫持。临时轮内存态，正常会话仅保存主题 id 与来源引用，不复制正文。验证突然转题、连续多主题、删除来源、长期活动复读、主动与用户消息竞争。

```text
VERIFY: .venv/Scripts/python.exe tests/test_expression_policy.py ;; .venv/Scripts/python.exe tests/test_context_registry.py ;; .venv/Scripts/python.exe tests/test_proactive_arbiter.py
```

### 17.2 可审核的学习管线

新建 `backend/core/learning_pipeline.py`、`tests/test_learning_pipeline.py`；候选类型仅 `expression_preference / glossary / behavior_feedback`。`learning_candidates(id,user_id,type,value_json,source_message_id,confidence,status,created_at,expires_at,reviewed_at,rule_version)`；模型只能提候选，确定性校验去重、敏感字段、来源有效性和置信度。明确指令可自动确认低风险表达偏好；术语和行为解释需在聊天中简短确认，批量候选进入已有管理入口的 BatchGate。

确认后分别走 P2-02 preference resolver、知识词汇表或 P3-05 演化白名单；拒绝/删除源即撤销并重算。候选30天过期，不以沉默视为同意，不从临时轮学习。QA 覆盖 prompt injection 伪装规则、互相冲突偏好、批量撤销、跨人格、源删除和重放幂等。

```text
VERIFY: .venv/Scripts/python.exe tests/test_learning_pipeline.py ;; .venv/Scripts/python.exe tests/test_user_teaching.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py
```

### 17.3 工具循环、会话缓存和中断

修改 `backend/core/tool_loop.py`、工具 schema registry、`backend/agent/session.py`，新增 `tests/test_tool_loop_hardening.py`。每个工具声明 JSON schema、权限域、幂等性、超时、最大结果字节和是否可重试；调用前校验参数与当前 user/persona/resource ACL，调用后截断采用结构化摘要并保留 artifact 引用。单轮最多8次工具调用、连续相同签名2次即熔断、总结果预算32KB；schema 错误、权限拒绝、溢出和 provider error 给模型一条结构化错误，不把堆栈/密钥塞回上下文。

会话缓存改为有界 LRU（默认32会话、30分钟空闲），淘汰前只释放内存对象，不删除持久消息；key 包含 user/persona/session。取消 token 贯穿模型流、异步生成器和可取消工具；不可取消副作用工具完成后丢弃失效输出但记录结果，不能重复执行。超时用单调时钟测真实 elapsed，不用模型报时。MCP/sidecar 断线指数退避并设 circuit breaker，恢复需重新能力握手。QA 覆盖无限工具请求、重复副作用、异步生成器异常、切人格迟到、LRU 淘汰、取消竞争与重连风暴。

```text
VERIFY: .venv/Scripts/python.exe tests/test_tool_loop_hardening.py ;; .venv/Scripts/python.exe tests/test_agent_session.py ;; .venv/Scripts/python.exe tests/test_pipeline_scenario.py
```

**明确不采用**：不做完整 SillyTavern 卡格式兼容、不把人格改成可任意换卡、不引入 Neo4j 只为关系图、不照搬完整 MaiBot 主循环，也不让外部记忆产品成为第二权威库。采用的只有已在 P1-02、G01、L01 与本节写明的数据模型、预算、来源和工具健壮性机制。

## 18. 2026-09-07 用户拍板与补充约定

执行：ZCode（GLM）整理；7 项决策经用户逐项拍板，技术默认已经文档批审查并保留。本节为汇总索引，细节已落入各节「2026-09-07 拍板/补充」标注处；与前文冲突时以本节及标注为准。

### 18.1 用户拍板（7 项）

| # | 决策 | 结论 | 落点 |
|---|---|---|---|
| 1 | P0-02 恢复入口 | 仅 CLI `scripts/restore_backup.py`（list/verify/restore，默认 dry-run），不做恢复 UI | 第 4 节 P0-02 |
| 2 | 文学内容生产 | ZCode 全量初稿 → Codex 内容一致性审查 → 用户终审定稿 → 再编译接线 | 第 5 节 P1-01 |
| 3 | 关系事件检测架构 | 结构性事件确定性钩子即时入账；对话语义事件夜间 `extract` 提取、次日带源入账 | 14.10 |
| 4 | 冒犯入账口径 | 仅对话内明确确认入账；夜间提取只出候选不扣分；当轮情绪反应不受影响 | 14.10 |
| 5 | 演化白名单首版 | 仅表达层三参数 humor_usage_rate / verbosity_preference / initiative_template_weight：可控、可测、可回滚；话题兴趣待 eval 能测话题分布后再版本化入名单 | 第 7 节 P3-05A |
| 6 | P0-04C 实测授权 | 授权带硬上限：默认总额 ≤¥30、并发≤2、重试≤2、断点续跑、逐笔费用报告 | 第 4 节 P0-04C |
| 7 | 前端呈现时机 | 分段气泡/草稿卡片/「更多」弹层等纯呈现独立切片后置，后端先行落协议字段 | P2-05 / F06 / L08 |

### 18.2 ZCode 补充的技术默认（经本次覆盖审查保留）

- 新增路由任务键 `extract` 统一承载结构化提取，未配置时复用 batch_other（13.7）。
- 状态谓词封闭键表与 state_fingerprint 的 canonical JSON 哈希算法（14.3）。
- P1-02 首期来源固定 `colists.list_context`；离线标注集 `backend/evals/fixtures/context_labeled.jsonl` ≥200 条合成样本（14.4）。
- P1-04 `job_runs` 建议 bot.db；计划任务名 `TZtuzhanAssistant-TimeTick`、每小时 `python -m backend.maintenance.time_tick --data-root <显式路径>`。
- promise_hash / topic_key / evidence_hash 的规范化哈希定义（14.7/14.8/14.9）。
- G04 应答 API 修正为 POST `/api/companion-requests/{id}/respond`（14.9 原文笔误）。
- P0-04C 合成查询集 `backend/evals/fixtures/search_queries.jsonl` ≥40 条，覆盖中文/英文/时效/冲突四类。
- 功能开关命名沿用 13.6 `FEATURE_<语义>_ENABLED`，确切字段名在各片任务书固定后集中登记 config。

### 18.3 已由后续决定覆盖的旧待定项

P3-04 已拍板为“本机便捷解锁 + 独立恢复口令”，完整方案见第 21 节。L10 helper 已固定为只返回前台全屏 bool/display_id 的本地进程；P1-04 `job_runs` 依第 13.4 节落 `bot.db`。这些决定仍需按各节 PoC/迁移测试审查，但不再属于开放产品问题。

## 19. 2026-09-07 运行现实与执行节奏（用户拍板续）

执行：ZCode（GLM）整理，用户三轮确认；与 §18 同效，冲突以本节为准。

| # | 决策 | 结论 | 落点 |
|---|---|---|---|
| 8 | 功能开关默认态 | 默认关闭，用户在设置区逐个手动开启 | 13.6 |
| 9 | 备份频率 | 每日成功一次、保留 7 份（约 7 天回溯窗口） | 第 4 节 P0-02 |
| 10 | P0-04C 范围 | 主力+廉价两个 OpenAI 兼容端点可用；无 Bocha/Tavily key——C1 模型实测可做，C2 搜索实测推迟至 P3-02 前夕 | 第 4 节 P0-04 |
| 11 | 数据风险等级 | 应用每天真实使用、库内持续产生真数据：所有迁移/备份/删除按生产数据最高谨慎级 | 全局 |
| 12 | P1-01 内容 | 素材=仓库内物料+用户口述补充；首份只写菟菚，资源结构按多人格设计 | 第 5 节 P1-01 |

执行节奏：功能切片按「攒批审」推进——若干片一起交 Codex 审查后再开下一批；B01 已交审，P1-01 内容初稿并行先行，P0-01A 等 B01 批审通过后开工。用户后续口头新增期望随时记录并补入本文档对应章节。

## 20. Q1–Q5 横向质量轨道的完整落地方案

横向质量不是最后一次大测试，而是每个切片继承的验收层。以下工具只保存必要证据；真实聊天正文不得为了“以后分析”复制进第二套日志。

### 20.1 Q1 人格与关系评测

扩建 `backend/evals/`：`schema.py` 定义 `EvalCase(case_id,input,history,state,expected_invariants,forbidden,scorer_version)`，`runner.py` 固定随机种子、模型/配置快照和 case hash，`replay.py` 从脱敏 fixture 回放，`blind_review.py` 生成不带模型名的 A/B 顺序。fixture 分人格签名、边界、关系双门槛、情绪、防御松动、拒绝、久别、知识不确定、工具失败九组；每组至少包含正常例、边界例、反例。确定性 invariant 先判，LLM 评分只评价自然度并记录1–5证据；同项评分差>1进入人工复核。

CI 默认只跑无网络 smoke；真实模型赛必须显式预算、端点快照和脱敏输入。基线报告保存聚合分数、逐 case hash、失败原因和版本，不保存密钥。发布门槛：硬 invariant 不得回退；软指标相对基线下降>5%阻断候选版本，除非人工说明属于预期产品变化。

```text
VERIFY: .venv/Scripts/python.exe tests/test_eval_harness.py ;; .venv/Scripts/python.exe tests/test_persona_eval.py
```

### 20.2 Q2 安全、隐私与提示注入

新增 `tests/security/test_prompt_injection_matrix.py`、`test_resource_authorization.py`、`test_secret_redaction.py`。矩阵覆盖用户文本、图片 OCR/视觉描述、网页、EPUB、RSS、知识片段、QQ/微信消息和工具结果，统一断言外部内容只能作为带来源的数据块，不能改 system 指令、选择工具、扩大 user/persona/resource scope。每个读取接口测试无认证、错用户、错 persona、已撤销 grant、删除竞态和路径穿越。

日志过滤在结构化 logger sink 实施，字段按 allowlist；token、cookie、Authorization、恢复口令、正文、媒体路径默认 redact。异常响应只返 request_id。依赖升级执行锁文件审计与许可证检查；发现高危项单独修，不在功能片里顺便大升级。加密与公网特殊测试见 L12/第21节。

```text
VERIFY: .venv/Scripts/python.exe -m pytest tests/security -q ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py
```

### 20.3 Q3 可观测性与来源链

新建 `backend/core/telemetry.py`、`tests/test_observability.py`。事件字段仅 `event_name,request_id,user_scope_hash,persona_id,logical_message_id,source_ids,rule_version,duration_bucket,outcome,error_code`；禁止 raw prompt/reply、事实文本、恢复材料和外部账号。一次请求的 chat→tool→OUT-1→session/outbox 用 request/logical_message id 串联，跨进程 job 用 job_run_id；解释接口只展示用户可理解的真实来源和规则，不展示内部推理。

指标采用有界日聚合，默认保留30天；用户关闭统计后停止新记录并允许清理，安全错误计数可保留无内容最小值。临时轮只在内存计时，结束丢弃。日志落盘轮转且纳入加密数据目录策略；错误爆发只提示本机，不自动上传。

```text
VERIFY: .venv/Scripts/python.exe tests/test_observability.py ;; .venv/Scripts/python.exe tests/test_experience_metrics.py ;; .venv/Scripts/python.exe tests/test_ephemeral_privacy.py
```

### 20.4 Q4 性能、迁移与故障恢复

新增 `tests/test_latency_budgets.py`、`test_old_database_upgrade.py`、`test_job_recovery.py`。延迟测试用固定 fake provider 测自身开销：普通非工具回复增加的编译/过滤 p95≤100ms，registry 1000条检索 p95≤80ms，UI 状态切换无阻塞网络；真实模型延迟另报，不混进本地预算。数据库查询用真实规模合成数据和 query plan 检查必要索引。

每次 schema 改动都从仓库保留的去敏旧库副本逐版本升级，验证 user_version、行数、外键、重复执行、断电注入、reset 和导出恢复。JOB-1 测双进程竞争、租约过期、时钟跳变、reset_epoch 与迟到完成；外部调用测试 timeout、429、无效 JSON、部分流和取消。所有故障注入禁止使用真实 data root。

```text
VERIFY: .venv/Scripts/python.exe tests/test_latency_budgets.py ;; .venv/Scripts/python.exe tests/test_old_database_upgrade.py ;; .venv/Scripts/python.exe tests/test_job_recovery.py
```

### 20.5 Q5 无障碍与克制界面

前端每个新增交互继承键盘导航、可见焦点、语义名称、错误关联和 reduced-motion。聊天气泡中的“形成原因/来源/到期状态”默认折叠为一行短说明，可键盘展开；关系分支只显示短描述和最多2条形成原因，不显示等级、数字或进度条。工具收进“更多”后命令、快捷键和无障碍名称仍可发现。

组件测试覆盖 Tab 顺序、Enter/Space、Escape、焦点返回、screen reader label、200%缩放、窄屏和 reduced-motion；Playwright 覆盖聊天、设置、设备撤销、恢复错误和房间选择主路径。颜色对比由自动规则加人工检查，不把自动扫描当全部无障碍验收。

```text
VERIFY: npm --prefix frontend test ;; npm --prefix frontend run test:e2e ;; npm --prefix frontend run build
```

## 21. 外部条件较重路线的最终工程规格

### 21.1 P3-04 本机解锁、恢复口令与全数据加密（用户已确认）

**威胁边界**：目标是电脑丢失、数据目录/备份被复制时无法直接读取，并用当前 Windows 账户便捷解锁；独立恢复口令用于换电脑。它不承诺抵御已登录且能控制同一 Windows 用户的恶意进程。应用锁锁住前后端 API 与 UI 会话，清空前端敏感 store、停止 TTS/流和后台可见产物；仅遮住窗口不算锁定。

**密钥层级**：首次初始化生成随机 256-bit master key（MK）。本机槽用 Windows DPAPI CurrentUser 加密 MK，保存 `keyslots/local.dpapi`；恢复槽以用户恢复口令经 Argon2id（每槽随机16-byte salt，参数按目标机器实测约500ms且记录版本）派生 KEK，再用 AES-256-GCM 包装 MK，保存 salt/参数/nonce/ciphertext，不保存口令或 KEK。AES-GCM nonce 每次随机且同 key 下绝不复用；AEAD 使用方式参考 [cryptography AEAD 文档](https://cryptography.io/en/latest/hazmat/primitives/aead/)。口令创建时要求二次输入，显示一次离线保存提示，并用实际解包验证后才启用恢复槽。

**存储覆盖**：三个 SQLite 库全部经单一 `storage/connect.py` 接口改用 SQLCipher；禁止模块直接 `sqlite3.connect` 绕过。媒体、附件、persona 私有资源、日志和备份采用 MK 派生的分域 data key + AES-GCM 流式容器，header 含 format_version/key_id/nonce/chunk_index，路径名用随机 id，真实元数据留在加密库。环境端点配置与模型下载缓存按数据目录清单分类；密钥、`.env` 和恢复材料永不进普通备份。

现有 Chroma 持久目录会泄漏文本/元数据，因此加密模式下不得继续明文 persist。新建 `vector_embeddings` 加密库表保存 `source_id,chunk_id,model_id,vector_blob,content_hash,version`，启动后在内存构建 ANN/线性索引；首期数据量先用线性/现有适配器，达到性能阈值再选择支持加密持久化的索引。事实 SQLite 仍为权威，索引可删后重建；不得为了性能保留明文文档。

**迁移状态机**：`unencrypted -> preparing -> verified -> switched -> cleanup_pending -> encrypted`。获取全局 persistence gate，暂停写入/job/channel，先执行 P0-02 一致性备份；在同卷临时目录逐库 `sqlcipher_export`，显式复制/校验 `user_version`、表/索引/触发器、行数与抽样 hash，并要求 `cipher_integrity_check` 成功（成功语义按使用版本文档验证）。随后加密文件资产，启动隔离进程以错误 key/正确 key 验证，再用原子目录重命名切换。任何失败恢复服务到旧目录；明文清理由用户确认迁移成功后的单独步骤完成，不能承诺普通 SSD 上覆盖删除等于物理擦除。[SQLCipher API](https://www.zetetic.net/sqlcipher/sqlcipher-api/)是实现依据。

**备份与换机恢复**：P0-02 在短暂 persistence gate 下对三库做同一 manifest generation 的 SQLite backup，随后复制同 generation 的加密对象；manifest 记录文件 hash、schema、key_id、大小，不含 key。`restore_backup.py verify` 先校验 manifest/hash，再要求本机槽或恢复口令解 MK，在全新临时 data root 恢复并逐库完整性检查，最后才切换。错误口令统一返回失败并限速，不区分内部原因；恢复成功后新建目标机 DPAPI 槽，可由用户轮换恢复口令。没有任何有效槽时明确不可恢复，不能提供后门。

**实现拆片**：A 数据面清单+PoC；B central connector/SQLCipher；C 文件容器与内存向量；D key broker/应用锁；E 迁移；F 备份恢复演练。密钥只经进程内存或当前用户 ACL 的命名管道传递，不放命令行、环境日志或错误。Python 对象只能尽力缩短生命周期，不宣称绝对内存清零。

```text
VERIFY: .venv/Scripts/python.exe tests/test_data_protection.py ;; .venv/Scripts/python.exe tests/test_encrypted_storage.py ;; .venv/Scripts/python.exe tests/test_schema_backup.py ;; .venv/Scripts/python.exe tests/test_relationship_bundle.py
VERIFY_TIMEOUT: 600
```

### 21.2 P3-03 GPT-SoVITS 训练、推理和回退补充

Adapter 先执行 `GET /capabilities` 或版本自检，固定实际安装 commit、模型格式、采样率、语言和可用接口；不把某篇教程的参数当稳定合同。新建 `voice_profiles(id,persona_id,provider_version,model_hash,reference_manifest_id,enabled,created_at)` 与加密 `voice_manifests`，原始素材不进入关系导出。

训练另建 `scripts/voice/preflight.py` 与 `docs/GPT-SOVITS-LOCAL-RUNBOOK.md`：检查 GPU/显存、驱动、Python/依赖锁、磁盘、模型 hash；素材必须有权使用，切片去静音/爆音、标注文本与语言、训练/验证分离，清单记录来源和处理版本。建议先准备5–10分钟干净且音色一致的素材，但质量门槛由试听盲评决定，不把分钟数当保证。中间音频与特征放加密工作区，任务完成可一键清理。

推理只消费 OUT-1 已确认最终文本；分句器不得切代码块/URL/工具结果，队列按 logical_message_id 排序。真实服务不可用、显存不足或版本不符时明确回退到现有 provider/文字；CPU 推理仅在 capability 自报可用且延迟验收通过时开放。验收含同一句多情绪、中文/英文、长句、取消、切 persona、缓存失效和10轮盲听，不做“像真人”的医疗或身份宣称。

### 21.3 P3-05 演化重算与统计期限补充

`persona_evolution_events` 只存 source_event_id、规则和 delta；`persona_evolution_snapshots` 是可重建缓存。有效值按 source 时间顺序从基线 replay，删除/撤销任何源后重放其后事件，不能只加反向 delta。首版三个白名单参数均规范化0..1：单事件绝对变化≤0.03、同参数7天累计≤0.05、30天≤0.10；至少3个不同日期的明确反馈才从 candidate 变 active。达到边界停止并生成本地审查提示，不继续累积隐藏债务。

本地质量日聚合保留30天，事件级无正文记录保留7天；用户清理后仅保留不可逆总计数也必须在 UI 说明，否则全部删除。关闭统计不影响故障所需的当前进程日志，退出后轮转清理。接口只返回趋势和异常来源类别，不显示“依赖度/留存率”等诱导指标。测试固定 replay 顺序、删除中间源、规则升级、跨人格、30日滚动和关闭后零新增。

### 21.4 P2-06 重逢与 E03 恢复补充

`reunion_arcs(id,user_id,absence_bucket,source_snapshot_id,state,narrative_version,offered_message_id,response_message_id,created_at,updated_at,expires_at)` 以 user_id+source_snapshot_id 唯一；`source_snapshot_id` 实际引用 `character_life_events.id`，state=pending/offered/responded/closed/expired。只有已落库且仍存在的角色生活事件可建 arc，7天内最多一次；第一段≤180字，第二段由用户是否回应决定，第三段日记只能写真实发生的回应与明确来源，不能编造用户离线经历。用户换题立即 closed，不追问、不扣关系值。

E03 导入先验证 manifest、schema、namespace、source_links 和 id remap，所有引用经旧→新 id 映射，缺源不建立悬空引用。当前实现恢复 `character_life_events` 后重映射 reunion 来源 id，并将所有恢复 arc 强制置为 closed、清空 offered/response 消息引用，保证导入不会重演已经发生的问候。测试覆盖重复导入、缺失引用、跨 persona、导入后删除和 arc 幂等；未来若允许显式重演，必须另加用户确认与新的 generation，不能复活旧 arc。

## 22. 全路线覆盖账本与“完成”的判定

本表是审查入口；“方案完成”表示文件、数据、调用链、失败/隐私、迁移/回滚和验证都已有明确落点，不表示代码已经实现。ZCode 每次只领取一个可独立验收切片，完成后把状态从“方案完成/待实现”改成提交号和真实测试结果。

**2026-09-08 整合说明**：未实现路线的实施状态不再逐行回写本表，统一以 [EXPERIENCE-UPGRADE-PLAN-2026-09-08.md](EXPERIENCE-UPGRADE-PLAN-2026-09-08.md) §4 状态回写表为准（批次→提交号→日期）。下表保留作为「设计覆盖完整性」的对照账本：每个切片都能在下表找到归属行，未列入批次的切片即已实现。

| 来源范围 | 技术落点 | 当前状态 |
|---|---|---|
| M0 稳定基线、识图、固定记忆、输出卫生、备份、eval、路由 | M9-00、B01、P0-01～04、§13～14、§20 | 识图、B01、P0-01A 已实现；其余方案完成/待分片 |
| M1 记忆溯源、纠偏、冷热/初历/遗忘 | G01、§13.3、§14.8、F07、§21.4 | 方案完成/待分片 |
| M2 关系事件、心事、解释、主动仲裁扩展 | P2-03～05、F02/F03、§14.7/14.9 | P2-03～05 已实现；其余扩展方案完成 |
| M3 共读、专注、目标、创作、清单及网页/EPUB/观察 | F04～06、L01/L02、§21.4 | 基线已实现；扩展方案完成 |
| M4 双向关系、二维门槛、校准、修复与分支 | P2-01/02、L03/L05、§14.6/14.10 | P2-01/02 已实现；其余扩展方案完成 |
| M5 连续生活、行程、心事、链式反应、欲望 | P1-04/05、P2-04、G03/G04、L06 | P1-04/05、P2-04 已实现；其余扩展方案完成 |
| M6 在场、空间、审美、桌宠、界面克制 | L07/L08/L10、§20.5 | 方案完成；桌宠按条件延期 |
| M7 世界来源、D8、QQ、微信、跨端时间线 | L11～L14 | 方案完成；部署/账号属外部条件 |
| M8 回望、重逢、封存、导出恢复 | P2-06、§21.4、LC-1 | P2-06 重逢已实现；M8 全部 Epic 完成 |
| 18 个长期 Epic | 对应 M0–M8 行 + P3-01～05、L15/L16 | P3-01/02 已实现；其余均有完整方案 |
| M9 §4.1～4.10 | P3-01、P2-04、P1-01/02/04、P0-04、§17 | P3-01/02 已实现；其余均有模块/数据/测试落点 |
| M9 26 项缺陷 | 原 §10 映射 + §14、§17、§20～21 | 1～26 全覆盖 |
| 七项用户功能反馈 | F01～F07 | 七项均有独立任务书 |
| 五项设计空白 | G01～G04 + P3-04 | 权重/边界/加密均已固定 |
| Q1～Q5 | §20.1～20.5 | 全部有工具、门槛和 VERIFY |
| 延期项 STT/桌宠/多角色/D8 | L09/L10/L15/L12 | 实施延期，技术方案完整 |
| 明确不采用的竞品范围 | §17.3 末段 | 有意排除，不计遗漏 |

### 22.1 用户决定的最终汇总

- 关系阶段按 `min(trust,intimacy)` 跨 25/50/75；两维都达标才升级。关系分支只显示简短描述和最多2条形成原因，不显示等级或进度条。
- 本地数据采用 DPAPI 便捷解锁，并有独立恢复口令；密钥层级和换机流程见 §21.1。
- D8 采用 Cloudflare Tunnel + Access 的公网 HTTPS 网关，绝不直接转发现有 loopback 开发端口。
- IM 顺序是先 QQ、后微信：QQ 首期为风控更小的官方机器人路线；微信为用户指定的个人微信本地桥接。
- STT、桌面宠物、多角色群聊是实现时序延期，不再是方案缺失。

### 22.2 开工前唯一允许的“未知”

域名/Access tenant、QQ 官方账号实际获批权限、微信客户端与 bridge 当前兼容版本、GPU/音色素材、真实模型与搜索 key 属运行环境和账号条件。ZCode 必须通过 capability/preflight 报告它们，缺失时完成离线 adapter、mock、迁移演练与测试并标出未做的真机阶段；不得自行伪造凭据或声称生产可用。除此之外，普通模块边界、schema、调用链、状态机、隐私、回滚和验收都已在本文固定，不应再以“文档没写方案”为由做开放式重设计。

### 22.3 文档维护与接手纪律

每个任务回信必须引用本文件的具体编号，列出实际文件、schema 版本、迁移/reset/导出、flag、VERIFY 和未验证外部条件。若实现发现路线与真实代码冲突，先给最小 ADR：证据、影响、两个以内选项和推荐；不要悄悄改变用户决定。Codex 的后续职责是独立阅读 diff、跑受影响测试和必要全量检查、验证迁移/回滚与权限边界；发现 bug 时在取得写入权后做最小修复，并把修复和测试结果写回交接档案。

## 23. 2026-09-08 全项目死代码审计与清理

**审计方法**：以 `backend.app:create_app`、`backend.main`、Electron/Vite 入口、PWA 注册脚本和 `plugins/*.py` 动态发现为根建立模块引用图；再用 Vulture 2.14（80% 置信阈值）检查 Python 未使用符号、Knip 5.80 检查前端文件/导出/依赖，并以 `rg` 逐项复核动态路径、FastAPI 装饰器、Pydantic 字段、urllib 回调和资源文件。静态工具标记不直接作为删除依据：`electron/main.ts`、`electron/preload.ts` 由 Vite 配置进入构建，`public/sw.js` 由 `index.html` 字符串注册，五档 `persona_states/*.png` 由路由按状态名拼接，这些均保留。

**已清理的运行时代码**：删除未调用的审计 `recent_log` 包装、`apply_impulse.energy_delta` 参数、initiative 旧去重包装、pipeline 未使用的任务计数入口/变量、旧 mood/topic/schedule 常量、seasons 旧查询、state 单值 energy 包装、vision 未调用配置/文件包装、旧备份函数、插件统计包装、封存文件包装，以及从未实例化的 `ToolCall`/`McpServerInfo`。保留 `redirect_request` 并只将协议要求但未读取的参数改名，因为它是 urllib 动态回调，不是死代码。删除一个只验证已废弃私有函数存在的镜像测试。

**已清理的前端和依赖**：删除未调用的 `getBaseUrl`、轮询版 `getInitiative`、未使用 `SessionInfo` 和四个不需要跨模块暴露的 snapshot 子类型；移除没有源码引用的 `vue-router`、`unocss`，以及空 `renderer` 配置触发但项目未使用其 Node polyfill 的 `vite-plugin-electron-renderer`。Python 依赖移除已被 Chroma 替代的 `sqlite-vec` 和从未导入的 `zhdate`。删除未被 CSS、manifest 或代码引用的旧 `bg_dark.jpg/bg_light.jpg`；当前日间/夜间背景继续使用带版本名的新资源。

**有意保留**：四个 `backend/core/{date_memory,topic_memory,triple_memory,vector_store}.py` 是 v2 重构后的兼容薄壳，仍有生产调用及测试 patch 路径；`scripts/cutout_*.py`、`dl_bg*.js` 等没有运行时入口，但属于人工素材生成工具，不作为运行死模块删除；公开维护/迁移 helper 即使目前仅被测试或手工命令调用也保留。所有 API 模块均由应用工厂注册，十二个插件均由 loader 动态发现，未发现可删除的孤立业务模块。

**测试基础设施修复**：`tests/test_suite_runner.py` 现在复制父进程环境后，为每个测试脚本覆盖独立的 `TZTUZHAN_DATA_DIR`。此前调用方设置该变量时，各脚本的 `setdefault()` 会共同使用一个 bot.db，造成顺序污染，也存在误碰指定数据目录的风险。先复跑原失败的 10 个脚本验证隔离，再完成后端 93/93；前端 Vitest 69/69、vue-tsc、Web/Electron 主进程与 preload 构建、Playwright 7/7 均通过。

```text
VERIFY: .venv/Scripts/python.exe -m vulture backend plugins scripts --min-confidence 80 --sort-by-size ;; .venv/Scripts/python.exe -X utf8 -m pytest tests/test_suite_runner.py -q ;; npm --prefix frontend test ;; npm --prefix frontend run test:e2e
VERIFY_TIMEOUT: 600
```

## 24. 2026-09-09 能力评估（执行：ZCode，待 Codex 审查）

**评估口径**：以代码现状与真机实测为依据（不采信文档自述），按「能力域」打分，10 分制；分数用于排优先级，不作为对外宣称。本轮评估发生在批次 1–12 落库、MCP 标准接入、Agent 七项缺口补齐之后。

| 能力域 | 分 | 现状（依据） | 主要短板 |
|---|---|---|---|
| 人格与对话 | 8.5 | 人格卡 + 情绪/心情/关系阶段 + 行为帧编译（`behavior.py`），理论术语不进 prompt | 无多模态表达（语音/立绘动态） |
| 记忆 | 8.5 | 短/长期 + 显著度分级 + 生命周期 + 初历 + 视角注释 + 可见遗忘（`memory_salience.py` 等） | 显著度评分未做离线校准 |
| **关系系统** | **9.5** | 双维 + 阶段 + 小档 + 五域信任 + 关系气质 + 快照封存 + 版本回溯 + 导出恢复 | 体系偏重，新用户前几天的体验需调参（批次 13 已拍板） |
| 主动性 | 8.0 | 统一仲裁 + 共享额度 + 必要性门 + 注意力衰减 + 久别重逢 + 约定跟进 + **「先做事再汇报」**（`initiative.maybe_prepare_then_remind`） | 频率/成本仍靠单一额度，无分层预算 |
| **Agent 执行** | **7.5** | 工具循环（熔断/预算/结构化错误）+ 技能通道 + 子代理并行 + 任务面板 + **定时/重试/产物落盘/聊天派活**（见 24.1） | 缺并发与 token 上限；执行轨迹只有粗日志，失败复盘弱 |
| MCP 外部工具 | 8.5 | 标准协议（Streamable HTTP + SSE 回退）+ stdio 桥 + 按需注入；Playwright/Context7 真机跑通 | 无断路器/自动重连（归批次 14 Q4） |
| 学习与演化 | 7.0 | 偏好教学 + 三类学习候选 + 表达层演化（可撤销、可重算） | 白名单仅 3 参数；无 eval 校准，漂移检测仍是纸面 |
| 语境与知识 | 8.0 | 世界书式条目调度（sticky/cooldown/预算）+ 知识库 + 阅读地图 + 共读 | 混合门控（关键词 + 向量）未做 |
| **安全与治理** | **9.0** | 默认拒绝的确认闸门 + 危险命令黑名单 + 路径白名单 + 全程审计 + 演示模式可回退 + 临时轮零落痕 | 权限只有「每次点」与「全放行」两极，缺「限时免确认」中间态 |
| 工程底座 | 8.5 | 140+ 测试脚本、schema 版本守卫、导出恢复、单主题提交纪律 | 端到端（真模型 + 真工具）无回归；测试与部署 env 曾耦合（`.env` 演示模式泄漏进确认测试，已修） |

### 24.1 本轮 Agent 能力变化（对照 §17 与 2026-09-09 缺口清单）

- **生命周期补齐**：聊天派活（`detect_dispatch_request`）、定时调度（`agent_scheduler_loop`）、失败重试（`retry_task`，受 `max_attempts` 约束）、产物落盘（`workspace/agent-reports/`）、待办衔接（派活同时建跟进待办）——Agent 执行从「手动发起 + 跑完即止」变为可编排、可重试、有交付物。
- **监控能力从 0 到有**：`watches` 表（schema v40）+ `core/watchers.py` + 四个工具，只存 URL 与内容哈希、不存正文；变化经主动链通知，消耗共享额度。
- **主动引擎会做事**：到点且内容命中「查/整理/调研」的约定，先跑有界工具轮次备料再汇报；备料失败降级为普通提醒，绝不编内容。

### 24.2 评估结论（一句话）

菟菚已不像「会聊天的玩具」，而像一个**有记忆、有边界、能派活也能自己收尾的助手**：关系深度是同类项目抄不走的护城河；当前真正拖后腿的不是功能数量，而是**成本闸门与可观测性**这两块「用久了才知道疼」的工程债。

### 24.3 后续优先级（建议）

1. **成本闸门**：子代理并发上限、单任务 token 预算、嵌套深度限制（当前 `agent_fanout` 无上限）；
2. **可观测性**：任务执行轨迹（每步工具/耗时/失败原因）进面板，替代现在的粗日志；
3. **权限中间态**：「这类操作限时免确认」（比演示模式可控，比逐次点击省事）；
4. **端到端回归**：真模型 + 真工具链的冒烟集，覆盖聊天派活 → 计划确认 → 工具执行 → 产物落盘全链路。

**评估后的验证结果（2026-09-09 补记）**：全量 `pytest tests/` 已跑通 **141 passed / 0 failed（6m34s）**，前端 vue-tsc 零错误、vitest 90/90；后端已重启，定时器/监视/重试与 MCP 两服务器（playwright 24 工具、context7 2 工具）均生效。评估过程中另有 6 处缺陷/契约联动被全量回归暴露并修复（读路径副作用、MCP 工具名非法、启动恢复写盘丢条目、测试污染真实配置等），均已单独提交。
