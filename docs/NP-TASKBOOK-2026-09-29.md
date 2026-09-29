# NP 批次任务书（v3）：心事门控补缺 + 向量召回权威闸门 + 文档回填

> 制定：ZCode（GLM），2026-09-29。依据：NP 批次一+二 14/14 完成后的全仓打磨点扫描（技术指导 §9 / BUG-HUNT 暂缓清单 / TECH-PLAN 状态板），两轮代码侦察已核实全部落点。
> 执行分工（按 AGENTS.md）：功能实现由 **ZCode** 承接；**Codex** 负责独立审查、验证与验收提交。
> **重要勘误**：技术指导 §9 所列七项切片中，「未完成心事旁敲侧击」与「记忆生命周期露出」**已有完整实现**（F03 @ `d9d169c`、F07 @ `1a181bb` + `371c98d`，含 `PendingThoughtProvider`、气泡内生命周期 UI 与测试）——前者仅剩本文 NP-15 一个缺口，后者无需立项。§9 表格的过期标注归 NP-17 回填。
> **行号说明**：本文行号基于 2026-09-29 工作区（含未提交的 netenv 直连批改动）。实现时一律以函数名/符号定位为准，行号仅作导航参考。

## 批次全局禁区（每个任务都适用）

- 派发/动工前先 `git status`：**未提交文件一律不碰**。当前已知未提交：无代理直连批（`backend/core/netenv.py`、`tests/test_direct_network.py` 及 README/config/tts/tool_loop 等约 34 个 M 文件）——该批归另一会话，本批次所有任务都不得触碰。
- 不升级任何依赖、不改 `package.json` / `requirements.txt` / 构建配置。
- diff 卫生：不做全文件重排版、不动无关行。
- 单主题单提交：`fix: NP-15 一句话` / `fix: NP-16 一句话` / `docs: NP-17 一句话`。
- 遵守 AGENTS.md 协作纪律：对方 30 分钟内有活动时只读不写。
- 后端测试遵循「脚本 + main()」约定（pytest.ini 只收 5 个文件，新脚本由 `tests/test_suite_runner.py` 自动收集，勿加入 `_SKIP` 排除表）；前端测试用 vitest + `vue-tsc --noEmit`。

## 建议执行顺序

NP-15 → NP-16（可同会话连续做完、分开提交）→ NP-17（收尾回填，把 NP-15/16 完成状态一并标进各文档）。

UAT 真机执行（更新后的 `docs/UAT-CHECKLIST-2026-09-20.md`）**不派发**，留在交互会话由用户手测。

---

## NP-15 心事旁敲侧击：event_chain 源存活判定补缺

> 状态：**已完成** @ `6150fdc`（2026-09-29，ZCode 实施，VERIFY 全绿：F03 门控 5/5 + M5 心事回归 4/4。自行补充决策：①任务书路线分歧定为路线 a——event_chains 有明确取消语义（status 五态），`_source_alive` 按 `status IN ('waiting','due')` 判定，done（回望已表达防重复）/cancelled（用户算了）/expired 一律失效；②主动路径 `next_thought_for_stage` 同步加同款源检查——`cancel_chain` 不级联作废心事，不补则主动路径仍会提起已取消回望；改动在 pending_thoughts 内部，未碰 initiative 的限额与仲裁）

**背景**：F03 已交付 `PendingThoughtProvider` 语境注入（registry 门控、bigram 相关性、阶段门控、4 回合冷却均已验收）。侦察确认验收重点四项中「冷却/用户拒绝/源过期/取消」都已由既有机制满足（registry 生命周期、`status != 'pending'` 过滤、`expires_at`、`forget_thoughts_for_source` 级联），**唯一功能缺口**：`_source_alive`（`backend/core/pending_thoughts.py:208-222`）只认 `activity`/`fact`/`open_question` 三种 source_type，而 `backend/core/event_chains.py:58-70` 会生产 `source_type="event_chain"` 的 `chain_aftermath` 心事——这类心事在语境门控路径被 `_source_alive` 直接判失效，**生产了却永远注入不了**。

**涉及文件**（修改）：
- `backend/core/pending_thoughts.py`：`_source_alive` 补 `event_chain` 分支；
- `tests/test_thought_context.py`：扩展用例。

**技术路线**：
1. 核实 `backend/core/event_chains.py` 的源表名与存活语义（状态字段/取消字段/过期时间），在 `_source_alive` 补分支：源行存在且未取消/未过期 → True。两种收敛都可接受、回信标注选型：
   - a. 源表有明确取消语义 → 按状态判定（与 open_question 分支同写法）；
   - b. 源表无取消语义 → 显式排除 `event_chain` 并注释「chain_aftermath 仅走主动路径，不入语境注入」——**但若选 b，需同时在 `event_chains.py` 的生产处注释说明该心事不进语境路径**，避免下一个读代码的人再当缺口报一次。
2. 不改 `context_registry.py` 生命周期机制、不改 `initiative.py` 主动路径（`maybe_express_pending_thoughts` 的每日限额与仲裁不动）。

**数据模型**：无 schema 变更。

**非目标**：不做「对话内说‘别提了’→心事冷却」的语义识别通路（涉及意图识别设计，需用户拍板后另批）；不接通 `commit_receipt` 生产调用（现状保守设计是有意的）。

**验收**：`tests/test_thought_context.py` 新增用例——event_chain 源存活时 `context_candidates` 可返回该心事；源被取消/删除后返回 None（若选路线 a）；既有用例（activity/fact/open_question）不回归。

**验收命令**：
```
VERIFY: .venv/Scripts/python -m tests.test_thought_context ;; .venv/Scripts/python -m tests.test_m5_thoughts
```

预估：1~2h。

---

## NP-16 P3-29 向量召回权威闸门（lm/kb）+ memory_search 跨分区碰撞

> 状态：**已完成** @ `35f0cbb`（2026-09-29，ZCode 实施，VERIFY 全绿：新增闸门 5/5 + knowledge_base/manager_memories/memory_v2/memory_salience 四套回归通过。自行补充决策：①memory_search 第 3 步兜底从薄壳 `vec_search`（丢 kind 的元组）换成带 `SearchHit.meta` 的完整版 `core.memory.vector_store.search`，分池逻辑抽为 `_pool_gated_hits` 便于直测；②提交纪律实证：userdb/long_term/knowledge 三文件带另一会话未提交 hunks，用 `git apply --cached` 只暂存本切片 hunks，对方改动完好留在工作区）

**背景**：BUG-HUNT P3-29（`docs/BUG-HUNT-2026-09-25.md:189`）：删除失败留下的孤儿向量被永久召回——已删对话原文/文档片段还会出现。facts 分区已有闸门（`db.recallable_fact_ids`），**lm 与 kb 两分区向量命中后直接用向量库存的文本、无回查**；重建链路（`migrate`）只重灌不做删除侧清理，救不了孤儿。顺带修 BUG-HUNT 第二轮新发现「memory_search 跨分区 id 碰撞返回错误记忆」（`backend/tools/builtin/memory.py:46-56` 向量兜底 `kind=None` 后只按 record_id 回查 `long_memory`，facts/topic 小整数 id 与 long_memory 碰撞时返回另一条无关记忆）。

**涉及文件**（修改）：
- `backend/core/userdb.py`：新增两个批查方法（仿 `recallable_fact_ids` `:2056-2072`、`manager_memory_existing_rids` `:1839-1848`，带 `@_locked`）；
- `backend/core/memory/long_term.py`：`recall()` 与 `_with_expansion()` 两处插入闸门；
- `backend/core/knowledge.py`：`recall_knowledge()` 插入闸门；
- `backend/tools/builtin/memory.py`：`memory_search` 回查按 kind 分池；
- 新建 `tests/test_vector_authority_gate.py`。

**技术路线**：
1. **userdb 新增**：
   - `recallable_long_memory_ids(user_id, ids) -> set[int]`：纯 `IN` 批查 `long_memory` 存在性（lm 无 status/expires 字段，pinned 不影响召回权）；
   - `existing_kb_chunk_ids(user_id, ids) -> set[int]`：`IN` 批查 `kb_chunks`，JOIN `kb_documents` 校验所属文档仍在。
2. **lm 闸门**（`long_term.py`）：
   - `_with_expansion()` `:153-162`：把 `allowed_fact_ids` 仅对 `kind=="facts"` 取值的三元扩成按 kind 分池——`kind=="lm"` 命中走 `recallable_long_memory_ids` 批量过滤；
   - `recall()` `:199-203`：向量命中 append 前同批过滤；
   - **fail-open 取舍**：查库异常时放行全部命中（沿用 mem 闸门 `memory_manager.py:459-461` 的既有取舍——闸门只防孤儿，不阻断召回）；
   - 判定为孤儿的 rid 收集后 `vector_store.delete_many(user_id, "lm", orphans)` 惰性清理（失败仅日志）。
3. **kb 闸门**（`knowledge.py:600-611`）：距离阈值过滤之后、append `hit.text` 之前，按 `hit.record_id` 批查 `existing_kb_chunk_ids`；不过者丢弃 + 同款惰性清理。
4. **memory_search 分池**（`tools/builtin/memory.py:46-56`）：利用 `SearchHit.meta["kind"]`（`vector_store.py:34-39` 已携带）分池回查：`lm`→`long_memory`、`facts`→`recallable_fact_ids`、**其余 kind（topic/diary/summary 等）命中显式丢弃**（宁少勿错；现状这些命中本就因回错表而基本被丢弃，修后行为等价但消灭碰撞错误）。
5. **测试**（`tests/test_vector_authority_gate.py`，脚本 + main()，临时目录隔离，假向量层仿 `test_knowledge_base.py:71-96` 的 monkeypatch 方式、断言风格仿 `test_manager_memories.py`）：
   - lm 孤儿：塞向量 + SQLite 行 → 删 SQLite 行（模拟 delete 失败留孤儿）→ `recall` 不返回该文本，且孤儿向量被惰性清理；
   - kb 孤儿：同上验 `recall_knowledge`；
   - fail-open：mock 查库抛异常 → 召回不挂、命中放行；
   - memory_search 碰撞：构造 facts id 与 long_memory id 碰撞场景 → 修后不再回错表；
   - facts 既有闸门回归不破坏。

**关键既有机制（必须复用/遵守）**：
- 闸门样例：`recallable_fact_ids`（userdb.py:2056-2072）、`manager_memory_existing_rids`（:1839-1848）；
- fail-open 先例：`memory_manager.py:459-461`；
- 惰性清理：`vector_store.delete_many`（:554-572）；
- **禁区**：不改 `rebuild_all`/`migrate` 的重灌逻辑（孤儿重灌后仍残留是有意行为，闸门是召回层兜底而非重建层职责）；不动 embedding 与 `vector_store.search` 契约；不动 facts/mem 分区现有闸门行为。

**数据模型**：无 schema 变更（纯查询函数）。

**非目标**：不做定期孤儿扫描后台任务（惰性清理足够）；不做 `kind=None` 全局检索的完整分池改造（仅修 memory_search 工具回查）；不动 `debug_vector_search.py`。

**验收**：新测试全绿 + 三套既有回归（knowledge_base / memory_v2 / manager_memories）不红；新测试会被 `test_suite_runner.py` 自动纳入全量套件（勿加 `_SKIP`）。

**验收命令**：
```
VERIFY: .venv/Scripts/python -m tests.test_vector_authority_gate ;; .venv/Scripts/python -m tests.test_knowledge_base ;; .venv/Scripts/python -m tests.test_manager_memories ;; .venv/Scripts/python -m tests.test_memory_v2
```

预估：4~6h。

---

## NP-17 文档状态回填 + UAT 清单增补（纯文档）

**背景**：多处规划文档状态已过期——最典型的是技术指导 §9 表格仍把已交付的 F03/F07 列为待办（直接导致本轮扫描误判）。集中一次回填，含本批次 NP-15/16 的完成标注。

**涉及文件**（修改，仅 docs/）：
- `docs/Zcode技术指导.md` §9 表格：逐行 grep 核实七项切片实际交付状态——「未完成心事旁敲侧击」「记忆生命周期露出」标已交付（F03 @ `d9d169c`、F07 @ `1a181bb`/`371c98d`）+ NP-15 增量；其余五行（主动问候去模板、四系统互通、专注收尾、共读方案 C、自动预填）**逐项核实后再标**：已交付标 commit，仍待做的保留，拿不准的标「待核实」——宁可待核实不可写错。
- `docs/TECH-PLAN.md`：
  - `:667` 附近「P3-03～P3-05 仍待后续独立交付」→ 按实际状态改写（P3-04 已交付，`vector_store.py:95` 注释即「P3-04 E」；P3-03/05 执行者 grep 核实后措辞）；
  - `:396` M6 语音输入条目：改为已交付——连续语音 v1 @ `26354b0`，勾选 `- [x]`，交互语义按现状写「说完自动发送」而非旧文案「先进输入框由用户确认」；
  - `:394` 桌面宠物条目与 `:677` 状态表 M6 行「桌面宠物与 STT 方向已定但延期」：PetView/petWindow 已存在，按实际交付面核实后更新措辞。
- `docs/BUG-HUNT-2026-09-25.md`：
  - `:22` P3-44 行加「已修 @ `a07a150`」；`:12`「8 项有意暂缓」与 `:16` 标题计数同步改为 7 项；
  - `:17` P3-29 行：NP-16 合入后加「已修 @ <commit>」（若 NP-17 先做，此行留给 NP-16 提交时顺手改，回信标注）。
- `docs/UAT-CHECKLIST-2026-09-20.md`：
  - 标题区加「适用版本 / 最后更新」字段（更新为 v3.5.0 / 2026-09-29）；
  - 补条目：连续语音 v1（按住说话 → 自动发送 → 朗读联动）；NP 批次关键面——首跑向导四步、迷你速聊窗热键问答、托盘/置顶/自启/全局热键、人格包 zip 导入后立绘跟随、消息重发/重新生成、数据保护区「立即备份/恢复演习」；语音条目旁注明 Firefox 下因 P3-53 暂不支持。

**非目标**：不改任何代码与测试；不重写文档结构，只做状态标注与条目增补。

**验收**：diff 仅含 docs/*.md；标注的 commit 号逐一与 `git log --oneline` 核对无误；UAT 清单新增条目与已交付功能一一对应。

**验收命令**：无（纯文档，人工审查 diff）。

预估：1~2h。

---

## 派发/执行说明

- 默认执行方 ZCode；若派 Codex worker：任务文本 = 本文件对应小节全文 + 全局禁区 + `VERIFY:` 行，`mode=edit`，`VERIFY_TIMEOUT:` 建议 300（本批无 vue-tsc，后端脚本较快）。
- 验收：Codex 独立审查 diff + 复跑 VERIFY；FAIL 时任务照常勾选但备注注明，由派发方审查。
- 每个任务完成即在本文档对应小节标注状态（`已完成 @ commit`），NP-17 收尾时同步回填其他文档。
- 本批之外：UAT 真机执行留在交互会话；M4 养成感主线（关系分支/幽默记忆/领域信任）与「对话内拒绝心事」语义识别需用户拍板设计后再立项。
