# KIMI 审查后的指导意见

审查：Codex 侧（Kimi K3），2026-09-07。
对象：B01 pinned 记忆契约修复（`backend/maintenance/loop.py` +58 行、新建 `tests/test_pinned_retention.py` 200 行 3 用例）＋ 文档批（`docs/persona/` 三份、`docs/Zcode技术指导.md`、`docs/M9-PRE-RESEARCH-2026-09-07.md`）。
方法：独立读码＋实跑验证（不采信自报全绿）＋两名独立校验代理 2/2 复核全部发现。状态：**待用户终审**。

---

## 1. 总体结论

B01 修复**方向正确、可以采信**：pinned 契约（永不因容量降级/删除，超阈值仅记计数提示）在代码、docstring、测试三层一致；连带的 `row_factory` 崩溃缺陷修复位置正确。9 项发现全部 minor，无 major/critical。

**定级背景**：旧代码缺 `row_factory`，只要存在任何待删行就必先 TypeError 崩溃——即旧「降级→销毁」路径在生产中从未真正执行成功，契约违反是潜在的；生产可见症状实为「清理长期不工作＋同周期审计日志轮转被跳过」（`maintenance_loop` 中 `clean_old_long_memory` 先于 `rotate_audit_log` 调用，同一 try 块）。B01 同时修掉了这两层。

## 2. 独立实跑验证

| 测试 | 结果 |
|---|---|
| `tests/test_pinned_retention.py`（新，3 用例） | ✅ all passed |
| `tests/test_memory_v2.py`（相邻回归） | ✅ 11/11 |
| `tests/test_ephemeral_privacy.py` | ✅ 通过 |
| `tests/test_state_interaction.py` | ✅ 通过 |

I2/I3 修补后复跑 `test_pinned_retention.py` 仍全绿。

## 3. 问题清单（9 项全部 minor，2/2 校验确认）

| # | 问题 | 建议 | 位置 | 状态 |
|---|---|---|---|---|
| I1 | `_report_pinned_overflow` 无去抖：永久超阈值用户每个维护周期重复输出同一条 INFO 日志（实测一次测试运行内同行重复 6 次）；调用点在删空判 return 之前，空转周期也记 | 「计数变化才记」，或首超 WARNING、后续 DEBUG | loop.py `_report_pinned_overflow` 及其调用点 | 留终审 |
| I2 | `LONG_MEMORY_KEEP` 行内注释仍写「long_memory 表保留的最新条数」，B01 后实际只约束 unpinned 行 | 改为准确语义 | loop.py 常量注释 | ✅ 已修（2026-09-07） |
| I3 | 测试文件 `_ids()` 辅助函数定义后无任何调用，死代码 | 删除 | test_pinned_retention.py | ✅ 已修（2026-09-07） |
| I4 | 三用例共享同一临时库、顺序耦合：`removed == 0` 断言依赖前序用例残留的全库 unpinned ≤100，中间插入新用例即脆弱 | 可接受不返工；若动，docstring 注明「依赖文件内顺序执行」 | test_pinned_retention.py 用例三 | 留终审 |
| I5 | 开关双层口径（13.6 env 层 vs features.py 动态层＋拍板 #8）未定义两层同赋一值时的优先级（即 §D6 待决项） | 终审拍板；建议「env 仅为部署默认，动态层覆盖」一句话收口 | Zcode技术指导.md 13.6/§18 | 留终审 |
| I6 | `promise_hash` 规范化只列操作未定顺序（去标点/压空白/lower 顺序敏感，14.8 topic_key 继承同歧义） | 定死顺序，建议 NFKC→lower→去标点→压空白 | Zcode技术指导.md 14.7 | 留终审 |
| I7 | 侧写 §7.11「不提植物/晒太阳/阳光/水」与正典 O-03「温水日常可偶提」字面冲突（语境上前者限菟丝子意象，但 O-03 无让渡条款） | §7.11 补「意象层面」限定，或给 O-03 加同款让渡 | 菟菚侧写档案.md 第七节；世界正典.md O-03 | 留终审 |
| I8 | 蕾拉 18 岁、每天下午来访 vs「很久以前出资建所/人去楼空」的年代张力 | 文档已自注「不做具体编年」，知会即可，无需改 | 世界正典.md C-02 | 知会项 |
| I9 | 14.3 状态键 `time_of_day` 无值域枚举，而侧写/正典已先用 `time_of_day=late` | P1-03 落地前在 14.3 补值域定义 | Zcode技术指导.md 14.3 | 留终审 |

## 4. G.1.2 裁决建议（待用户终审拍板）

### 4.1 keep 全局语义算不算缺陷？——建议：不算缺陷，判「已知语义不一致，记录在案」

- 热路径 `prune_long_memory`（per-user 800，userdb.py）每轮对话先生效；维护循环的全局 2000 只是兜底巡检。
- 单用户 1–2 人格时 unpinned 总量 ≤1600，全局线永不触发；≥3 人格且各存满 800 时，全局 2000 才会侵蚀不活跃人格的配额——超出当前部署形态，且全局保留天然偏向活跃人格（其记忆 id 最新），行为可解释。
- 改 per-user 属新切片（含迁移与测试），不应压进 B01 返工。

### 4.2 800/2000 双阈值要不要对齐？——建议：数值不对齐，注释对齐

- 三个阈值测的是不同的量：`PINNED_MEMORY_KEEP=800`（pinned 观察线/人）、`_LM_MAX_ROWS=800`（unpinned 热路径/人）、`LONG_MEMORY_KEEP=2000`（unpinned 兜底/全局），数值相等反而是误导。
- 风险点仅在 `PINNED_MEMORY_KEEP` 与 `_LM_MAX_ROWS` 同为 800 纯属巧合——已随 I2 在注释中注明「同值无联动」。
- 若用户终审拍板改 per-user 配额，再统一收口到单一常量来源。

## 5. 文档批意见

- **M9-PRE-RESEARCH-2026-09-07.md**：质量高，可采信为后续切片开工依据。G.1 诚实清单 5 条抽查属实（G.1.4 测试 id 形态、G.4.1 契约闭环与源码一致）；§D6 自我修正透明；「71 脚本 / test_memory_v2 11 通过」声称与实测相符。
- **Zcode技术指导.md**：13–16 节契约密度可执行，VERIFY 标记齐全，拍板/补充标注与 §17/§18 汇总自洽；跨文档一致性抽查通过（14.10 阶段阈值 ↔ 侧写 §8；F01 四类变体 ↔ 变体池；铁律 3 ↔ L02 不写回正典）。开放缺口即 I5/I6/I9。
- **persona 三份**：内部一致性好（阶段名、羁绊阈值、风格红线互查通过；问候变体池引「母本规则 7」已核对母本 persona-菟菚.md 第 62 行无误）。需终审裁量仅 I7；I8 文档已自限。

## 6. 已落地改动（2026-09-07，未提交）

1. `backend/maintenance/loop.py`：`LONG_MEMORY_KEEP` 注释改为「全局（非 per-user）保留的最新未固定条数」，并注明与 per-user 800 的分工及同值无联动——G.1.2 双阈值说明随之落地。
2. `tests/test_pinned_retention.py`：删除无人调用的 `_ids()` 死代码。

## 7. 终审待办汇总

- [ ] I1 日志去抖（建议「计数变化才记」）
- [ ] I4 测试顺序耦合注明与否
- [ ] I5 开关双层优先级（§D6，建议「env 默认、动态层覆盖」）
- [ ] I6 promise_hash 规范化定序
- [ ] I7 温水条款措辞（建议 §7.11 补「意象层面」限定）
- [ ] I8 年代张力（知会）
- [ ] I9 14.3 补 `time_of_day` 值域
- [ ] G.1.2 两项裁决（见第 4 节建议）
- [ ] 文档批整体定稿（persona 三份定稿前不接 `build_system_prompt`）
