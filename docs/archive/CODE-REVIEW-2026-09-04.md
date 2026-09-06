# 代码审查报告 —— 人格热切换 + D3 共读（2026-09-04）

> 审查范围：工作区全部未提交改动（persona 热切换、D3 共同活动/共读，及二者向 35 个存量文件的扩散修改）。
> 结论先行：**架构方向正确，主线实现扎实，但发现 3 个确认 bug（全部集中在"用户身份上下文边界"处）+ 一批人格名残留**。建议按 P0 → P1 → P2 顺序修复后再提交。

---

## 一、本次改动概览（Codex 快速补课）

两块新功能交叉落地：

1. **人格热切换**（新文件 `backend/core/persona_profiles.py`、`backend/api/personas.py`、`frontend/src/components/PersonaSwitcher.vue`）
   - 人格卡库存于 `data/personas/<id>/`（persona.md + settings.json），`active.json` 记录当前激活 id。
   - 数据隔离方式：**不改表结构，靠 user_id 命名空间**——默认人格沿用旧 id `assistant-main`（兼容存量数据），其它人格用 `assistant-main::persona::<id>`（见 `scoped_user_id()`）。
   - 会话库同理：公共会话 id 仍是 `current`，存储层经 `session_storage_id()` 映射到 `current::persona::<id>` 私有行；archives 表新增 `persona_id` 列过滤。
   - 全链路身份切换：30+ 处原硬编码 `"assistant-main"` 的 API/核心模块改为 `active_user_id()`；各 LLM prompt 里的"菟菚"用 `JUDGE_PROMPT.replace("菟菚", persona_name)` 方式替换。

2. **D3 共同活动（首期共读）**（新文件 `backend/core/activities.py`、`backend/api/activities.py`、`frontend/src/components/ActivityPanel.vue`）
   - 新表 `activities` / `activity_notes`（userdb.py，均含 user_id，已纳入 reset/rename 迁移清单）。
   - 对话侧：`pipeline.py` 新增 3.0.1 步 `active_reading_context(user_id, text)`，仅当用户消息命中阅读关键词（`_READING_CUE_RE`）才注入当前段落原文 + 书签，带 `<reading_excerpt>` 不可信引用声明（防文档内指令注入，写法正确）。
   - 知识库删文档时级联删除关联活动（`knowledge.delete_document`）；彻底重置时 `clear_user_documents` + 按 user_id 删表。

## 二、已验证无误的部分（不用重查）

- **reset 按人删除 schema 安全**：`_TABLES` 全部 24 张表（含 kv_store/users）均有 `user_id` 列，`DELETE FROM {table} WHERE user_id=?` 不会因缺列炸事务。
- **`vector_store.clear_user()`**：metadata 里确实写入了 `user_id`（`vector_store.py:160`），`col.get(where={"user_id": ...})` 匹配正确。
- **知识库级联**：删文档 → 删活动笔记 → 删活动 → 删分块 → 删向量 → 删落盘原文，顺序和异常兜底都对。
- **`activity_notes` 的 `ON CONFLICT(activity_id, position)`** 与表上的 `UNIQUE(activity_id, position)` 匹配。
- **sessions 库迁移**：`ALTER TABLE archives ADD COLUMN persona_id` 用 try/except OperationalError 幂等；`_ensure_session` 保证人格私有 current 行存在，`get_messages` 不会误报 404。
- **聊天主链路身份正确**：`api/chat.py:125` 在 `_runner` 里显式 `current_user_id.set(_user_id(session_id))`，工具层 `tools/base.py` 用 `contextvars.copy_context()` 传递，sync 工具在线程池里也不丢。
- **生成期间禁止切人格**：`personas._busy_response()` 检查 `chat._bg_tasks`，覆盖了 SSE 后台生成任务。

## 三、P0 —— 确认的 Bug（提交前必修）

### Bug 1：`llm._record_usage` 的兜底分支永远不生效

`backend/core/llm.py:129`：

```python
log_usage(current_user_id.get() or active_user_id(), channel, model or "", pt, ct, estimated)
```

`current_user_id` 的默认值是字符串 `"assistant-main"`（`backend/core/current_user.py:11`），永远为真，`or active_user_id()` 永远走不到。

- **影响**：聊天主链路没问题（chat.py 显式 set）。但**维护循环触发的 LLM 调用**（每日总结 `daily.py`、日记、约定提炼、主动消息 `initiative.py`、久别问候 `greeting.py`）从不设 contextvar → 激活非默认人格时，这些调用的 token 用量全部记到 `assistant-main`，而用量面板读的是 `usage_summary(active_user_id())` → **非默认人格的成本面板漏记全部后台消耗**。
- **修法**（二选一，推荐前者）：
  1. `current_user.py` 把默认值改为 `None`（类型 `ContextVar[str | None]`），`or active_user_id()` 兜底即可生效；同步改 `tools/base.py:264` 的 `get("assistant-main")` 为 `get(None) or ...`。
  2. 或在 `maintenance/loop.py` / daily 批处理入口显式 `current_user_id.set(uid)`。

### Bug 2：Agent 任务执行时工具写入身份错位

`backend/agent/session.py` 的 `run_task()`（212 行起）从不设置 `current_user_id`——全仓库只有 `api/chat.py:125` 和 `api/remote.py:102` 会 set。工具层 `backend/tools/base.py:264` 用 `current_user_id.get("assistant-main")` 决定记忆/待办/好感度写入归属。

- **影响**：人格功能上线前 `task.user_id` 恒为 `assistant-main`，所以此坑无感；现在切到人格 Luna 后创建 Agent 任务，`task.user_id` 是 `assistant-main::persona::luna`（system prompt、任务落库都对），但**任务执行期间所有工具写库（记忆/待办/事实）和 LLM 用量都落到 `assistant-main`**，跨人格串数据。`api/remote.py` 同理（uid 是 `"remote"`，这个是有意隔离、可不动）。
- **修法**：`run_task` 开头加：
  ```python
  from ..core.current_user import current_user_id
  current_user_id.set(task.user_id)
  ```
  （remote.py:99-102 已有同款先例写法，照抄即可。）
- **连带**：`api/personas.py` 的 `_busy_response()` 建议把 `agent._agent_bg_tasks` 也纳入"生成中禁止切换"判断（目前只查 `chat._bg_tasks`）。Agent 执行中切换人格，任务自身命名空间不会错（uid 已捕获），但与 Bug 2 叠加时工具写入会落到错误人格。

### Bug 3：`test_edge_regressions.py` 归档搜索测试现在必挂（环境耦合）

本次审查实测：`pytest tests` → **55 通过 / 1 失败**，失败项 `suite_archive_search_treats_wildcards_literally`（`tests/test_edge_regressions.py:114`，断言 `[] == ['literal']`）。

- **根因**：`session/store.py` 的 `_search_archives_sync` / `_list_archives_sync` / `_get_archive_sync` / `_archive_current_sync` 新加了 `WHERE persona_id=active_id()` 过滤，而 `active_id()` 读真实 `data/personas/active.json`。测试只 monkeypatch 了 `store._DB`，插入的归档 persona_id 是默认 `'default'`；**只要本机激活过非默认人格**（当前正是：`菟菚-tù-zhàn-…特化版`），查询就查不到 → 测试挂。产品行为本身是对的，是测试没隔离全局人格状态。
- **修法**：该测试加 `monkeypatch.setattr(store, "active_id", lambda: "default")`（store 是模块顶层 `from ..core.persona_profiles import active_id` 导入，patch store 命名空间即可）。顺带检查 `test_edge_regressions.py` 其它直接调 store 同步函数的用例是否有同样暴露。

## 四、P1 —— 人格名残留（切到非默认人格后行为/文案不一致）

人格切换改造主要覆盖了 pipeline/daily/memory 链路的 prompt；以下位置仍硬编码"菟菚"：

| 位置 | 性质 | 说明 |
|---|---|---|
| `backend/core/vision.py:83` | **行为级** | 识图 system prompt 仍以菟菚人设回复；建议仿 `perceive()` 加 `persona_name` 参数，由 pipeline 传入 `persona_name_for_user_id(user_id)` |
| `backend/core/state.py:193,197` | **行为级** | `addresses_her = "你" in compact or "菟菚" in compact`——用户直呼新人格名字时 state 机识别不到；需按 user_id 取人格名参与匹配 |
| `backend/api/remote.py:109` | 文案 | 远程任务 system prompt `你是菟菚助手` |
| `backend/core/memory_correction.py:24` | 文案 | 记忆仲裁 prompt |
| `backend/core/llm.py:314` | 文案 | 称呼提取 prompt |
| `backend/api/keepsake.py:32,60` | 文案 | 纪念册渲染署名/标题；archives 已有 persona_id，可顺带映射人格名 |
| `backend/core/stickers.py:72`、`core/proactive_media.py:45` | 待定 | 生图锁定菟菚外貌（绿发/菟丝子藤蔓）。若表情包/主动配图只打算给默认人格用则合理，需产品决策 |

## 五、P2 —— 小问题与建议

1. **死代码**：`api/diary.py`、`api/memory_admin.py` 里 `_user_id(session_id) if session_id else active_user_id()` 两分支已完全等价（`_user_id` 现在忽略参数、恒返回 `active_user_id()`），二选一保留。
2. **性能**：`persona_profiles.active_id()` 每次调用都跑 `ensure_library()`（mkdir + 多次 stat + 读 active.json）且持全局 RLock，而它处在每个 API 请求、每条 usage 记录的热路径上；`persona_name_for_user_id()` 每条消息重读 settings.json 多次（perceive / 向量索引 / 三元组 / 末尾落库各一次）。建议：`active_id()` 加进程内缓存 + `active.json` mtime 校验失效；profile 读取同理。
3. **`_user_id` 形参保留但失效**：`api/chat.py:20` 的 `_user_id(session_id)` 已不看 session_id，注意别让后来者误以为传会话 id 有意义。

## 六、修复后验证清单

```bash
# 后端全量（约 6 分钟；含 test_suite_runner 逐脚本子进程执行）
.venv\Scripts\python.exe -m pytest tests -q

# 前端（本次基线 19/19 全绿，人格/共读改动不涉及则应保持）
cd frontend && npx vitest run
```

- 重点回归：`test_persona_switcher.py`（人格隔离）、`test_activities.py`（共读）、`test_edge_regressions.py`（Bug 3）、`test_http_endpoints.py`。
- Bug 1/2 的手工验证：切到非默认人格 → 跑一次 Agent 任务 → 查 `facts`/`usage_log` 是否落在 `assistant-main::persona::<id>` 命名空间。
- 注意：本机 `data/personas/active.json` 当前激活的是非默认人格——这既是 Bug 3 复现条件，也是验证 Bug 1/2 修好的现成环境。

## 七、测试基线（2026-09-04 审查时）

| 套件 | 结果 |
|---|---|
| 后端 `pytest tests` | 55 通过 / **1 失败**（Bug 3，非产品缺陷） |
| 前端 `vitest run` | 19/19 通过 |

排除项不变：`test_live_chat.py` / `test_recall_real.py` 需真实 LLM/网络，CI 不跑。

---

## 八、工程约定提醒（沿用 HANDOFF-TO-CODEX.md，新增两条）

- 原有约定全部有效：pipeline.py 局部导入 config、用 `.venv\Scripts\python.exe`、测试是"脚本 + main()"风格由 test_suite_runner 子进程跑、`data/` 勿动。
- **新增**：`session/store.py` 与多个 api 模块现在依赖全局人格状态（`active_id()`）。任何直接调 store 同步函数的测试必须 monkeypatch `store.active_id`，否则测试结果取决于开发者本机激活了哪个人格。
- **新增**：新建后台任务/新链路时，凡是写 userdb 的路径，先确认 `current_user_id` contextvar 在该链路被显式 set（chat、remote 已做；agent 缺——见 Bug 2）。contextvar 默认值 `assistant-main` 是个静默陷阱。
