# 继续点交接 · 2026-09-06（GLM 停止工作时的快照）

> 用途：GLM 按"连续三次失败即停止"的约定退出时的现场快照，供用户/Codex 接着干。
> 结论先行：**没有发现任何真实回归**；测试失败是与 Codex 进行中的未提交改动"赛跑"导致的假红。

## 1. 仓库当前状态

- 已提交基线（`git log` 最新 8 条）：
  - `564b039` feat: add m3.2 focus companion with quiet mode and wrap-up（Codex，M3.2 专注陪伴）
  - `0b14f80` persona: move sunlight constraint into persona card with self-check list
  - `a8a9fe1` feat: complete m6 first slice with shared corner and voice prosody（M6 首批）
  - `63de398` feat: complete m5 pending thoughts with narrative planner（M5）
  - `38983c0` / `12dfca5`（M4.2 / M4.1）、`d456b45`（Sprint 2）、`21ae4be`（Sprint 3 / M3.1）、`0b803c2`（Sprint 1）

## 2. Codex 已提交并经 GLM 实测验证的进度（M3.2 专注陪伴）

- 验证结果：`tests/test_focus.py` 5 组全过（状态机/互斥/惰性到点/复盘资格/语境门控）、前端 Vitest **32/32**（含 focusMode 4 例）。
- 实现要点（摘自 `EVOLUTION-ROADMAP.md` 迭代记录）：复用 activities 表（kind='focus'，schema v6）；主动引擎三处仲裁点静默；惰性到点结算 + `focus_finished` 事件（第五类注册事件）；复盘不绩效评判、走既有 enqueue_proactive 且不占每日额度；共读/专注互斥且抢场时剩余时间冻结。
- ⚠️ 两处文档待修：
  1. `EVOLUTION-ROADMAP.md` M3.2 记录署名为"Kimi 执行"，实际是 **Codex** 执行；
  2. `HANDOFF-TO-CODEX.md` 头部基线仍是旧的"50/50 + 28/28"，未纳入 M3.2（应更新为后端 55/55、前端 32/32——以全绿套件实跑为准）。

## 3. Codex 正在进行中的未提交改动（勿动！）

`git status` 显示工作区有一批**未提交**改动，是 Codex 正在实施的 **M3.3 共同目标**：

- 新文件：`backend/core/goals.py`、`backend/api/goals.py`、`frontend/src/api/goals.ts`、`tests/test_goals.py`
- 修改：`backend/core/userdb.py`（`_SCHEMA_VERSION` 已升到 **7**，预计新增 goals 表）、`reset.py`、`pipeline.py`、`pending_thoughts.py`、`relationship_events.py`、`app.py`、`ActivityPanel.vue`（+222 行）、`CornerPanel.vue`、`ActivityPanel.test.ts`
- `reset.py` 的表清单已同步（+1 表），说明数据契约大体已铺完

## 4. 测试"连续失败"的真相（GLM 停止的原因）

- 现象：`test_schema_backup.py` 在全量套件与单独复跑中共失败 2 次（第 3 次尝试前按约定停止）。
- 根因：`tests/test_schema_backup.py` 的 `versions = {"bot.db": 6, ...}` 落后于工作区 userdb 的 `_SCHEMA_VERSION = 7` → 第 84 行 `assert schema_version(conn) == expected_version` 必然失败；断言抛出后 `db.conn.close()` 未执行，Windows 文件句柄占用又让 TemporaryDirectory 清理抛 PermissionError（次生噪音，掩盖真实断言）。
- GLM 首次单独跑通过，是因为当时 userdb 还是 v6——随后 Codex 把它改到 v7。
- **处理方式**：这是 Codex 切片未完成时的正常中间态，GLM 未越俎代庖去改。Codex 收尾时应把 `versions` 更新为 `{"bot.db": 7, ...}`（这是它切片的一部分）。

## 5. M3 剩余待办（接手清单）

1. **M3.3 共同目标**：Codex 进行中，等它收尾（含 `test_schema_backup.py` 版本号随 v7 更新、路线图/交接文档基线刷新、独立提交）。
2. **M3.4 共同创作**：未认领——轮流续写、世界观、歌单/书单、观察日志；所有产物版本化（artifacts 表已就绪）、虚构世界与现实记忆严格隔离。
3. **网页/EPUB 再评估**：M3.1 顺延项，路线明确"不与首轮同时做"，现在首轮已过，可评估。
4. M3 全部收尾后：`TECH-PLAN.md` 状态看板 M3 行改为"退出标准核对"，并对照 M3 退出标准（开始/暂停/恢复/完成/取消/导出、重启续接、语境门控）逐项核对。

## 6. 恢复验证的标准动作（等 Codex 提交后执行）

```powershell
# 1) 全量后端（预期 55+ 全绿，具体数字以 tests 目录脚本数为准）
.venv\Scripts\python.exe -m pytest tests\test_suite_runner.py -q
# 2) 前端
cd frontend ; npm test ; npx vue-tsc --noEmit ; npx vite build
# 3) 浏览器关键路径
cd frontend ; npx playwright test
```

## 7. 其他长期遗留（非 M3）

- M4：互动气质 / 幽默记忆 / 领域信任（E05–E07）
- M5：低频生活事件模板池 / 精力有限选择 / 惊喜编排
- M6：桌面宠物与 STT **待用户拍板**（TECH-PLAN 第 10 节）、共同审美、物件类型扩展
- M7/D8：公网资源就位前不动
