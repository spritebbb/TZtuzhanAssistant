# 代码审查报告 V8（单一会话模式改造后全量审查）

> 审查范围：拟人核心层 + 会话/归档系统 + 主动性引擎 + Electron 主进程 + 前端会话 UI
> 审查方式：逐文件通读，按严重程度分级

---

## 一、结论速览

| 级别 | 数量 | 说明 |
|------|------|------|
| 🔴 P0（严重，需立即修） | 0 | 无崩溃级 bug |
| 🟠 P1（高，建议尽快修） | 3 | 功能正确性 / 数据一致性隐患 |
| 🟡 P2（中，择机修） | 4 | 边界 / 健壮性 / 代码异味 |
| 🔵 P3（低，可忽略） | 3 | 死代码 / 风格 / 潜在隐患 |

---

## 二、P1 级问题（建议尽快修）

### P1-1：`behavior.py` 的 `_reaction_line` 存在死代码 + 变量名误导

**位置**：`backend/core/behavior.py:89`

```python
def _reaction_line(s: AgentState) -> str:
    hits = pending_emotion_hits if s.emotion_memory is not None else None  # ← 死代码
    if not s.emotion_memory:
        return ""
```

**问题**：
1. `hits = pending_emotion_hits if ...` 把**函数对象**（`pending_emotion_hits`）赋给 `hits`，而非调用它。这是一行从未被使用的死代码，且语义上明显是「想调用但写错了」。
2. `pending_emotion_hits` 这个 import 实际上**只在这一行死代码里用到**，删掉这行后 import 也应一并清理。

**影响**：功能正确（`_reaction_line` 实际用 `s.emotion_memory[-1]` 取最近情绪残留，逻辑是对的），但死代码误导后人，且暗示作者曾想走 `pending_emotion_hits` 路径。

**建议**：删掉 `hits = ...` 这行，并移除 `pending_emotion_hits` 的无用 import。

---

### P1-2：`initiative.py` 的 `_eligible_users` 直接裸访 `db.conn`，无锁

**位置**：`backend/core/initiative.py:66`

```python
rows = db.conn.execute("SELECT user_id FROM users").fetchall()
```

**问题**：`UserDB` 的所有写方法都经 `@_locked` 装饰器持有 `self._lock`（RLock）。这里直接 `db.conn.execute(...)` 绕过锁。虽然 SQLite 单连接读操作通常安全，但：
1. 与正在进行的写事务并发时，可能读到中间态或触发 `sqlite3.OperationalError: database is locked`（WAL 模式下读一般不受写阻塞，但同连接并发仍不干净）。
2. 项目其他所有查询（如 `get_user`、`recent_messages`）虽也裸访 conn 但只读，此处一致，属于「约定俗成但脆弱」。

**影响**：低概率并发异常，主动性后台循环里抛异常会被外层 `try/except` 吞掉（`_eligible_users` 内已有 `try/except`），最终表现为「本轮不主动」，不会崩溃。

**建议**：`_eligible_users` 里的 `db.conn.execute` 用 `with db._lock:` 包裹，或复用 `db` 提供的只读方法。

---

### P1-3：Electron 退出归档存在「重复归档」边界

**位置**：`frontend/electron/main.ts:292-296` + `backend/session/store.py:_archive_current_sync`

**问题**：`before-quit` 会调 `archiveSessionOnQuit()`，而托盘菜单「退出」按钮走的是：
```python
click: () => { isQuitting = true; stopBackend(); app.quit() }
```
即先 `stopBackend()`（优雅关闭后端，POST `/api/health/shutdown` + 1.5s 后 kill），再 `app.quit()` 触发 `before-quit` → 再调 `archiveSessionOnQuit()`。

**影响**：托盘退出时，`stopBackend()` 已经向后端发了 shutdown 请求并准备杀进程，紧接着 `before-quit` 里的归档请求可能：
- 后端已被 shutdown 流程关停，归档请求失败（静默，归档丢失）
- 或后端仍在处理 shutdown，归档请求撞上关闭窗口，同样失败

**结果**：通过托盘「退出」按钮退出时，当前会话**可能不被归档**（或归档丢失）。而「关闭窗口」按钮走的是 hide 到托盘，不会触发 quit，也就不会归档——这与用户「关窗自动归档」的原始诉求有偏差。

**建议**：统一退出路径，确保 `stopBackend()` 之前先完成归档。例如把归档逻辑前移：托盘退出按钮改为先 `await archiveSessionOnQuit()` 再 `stopBackend()` 再 `app.quit()`；同时 `before-quit` 里避免重复归档（加个已归档标记）。

---

## 三、P2 级问题（择机修）

### P2-1：`chat.py` 首帧 `session_id` 语义在单一会话下冗余

**位置**：`backend/api/chat.py:121`

```python
yield _sse({"session_id": session_id})
```

单一会话模式下 `session_id` 恒为 `current`，前端 `onSessionId` 已是空实现（注释「无需处理新建」）。首帧这个字段属于多会话时代的遗留，无害但冗余。

**建议**：可保留（向前兼容），若追求整洁可去掉。

---

### P2-2：`store.py` 保留了整套多会话 CRUD 死代码

**位置**：`backend/session/store.py` 的 `_create_sync`、`_delete_sync`、`_rename_sync`、`list_sessions`、`create_session`、`delete_session`、`rename_session`、`search_messages` 等。

单一会话模式下，这些函数：
- 前端已不再调用（`sessions.ts` 已删除 `listSessions/createSession/deleteSession/renameSession/searchMessages`）
- 但 `api/sessions.py` 仍保留 `GET ""`、`POST ""`、`DELETE /{id}`、`PATCH /{id}`、`/search/all` 等路由

**影响**：无功能 bug，但「单一会话」的设计意图被这些遗留 CRUD 稀释，且 `POST /api/sessions` 仍能创建多会话（虽然前端不用）。若有人（或插件）误调，会造出孤儿会话。

**建议**：确认无其他引用后，移除或禁用这些多会话 CRUD，仅保留 `current` 会话 + 归档三接口。

---

### P2-3：`greeting.py` 的 `greeting_for` 在单一会话下 `session_id` 恒为 `current`

**位置**：`backend/api/greeting.py:18` + `backend/core/greeting.py:121`

`greeting_for(_user_id(session_id), session_id)` 中 `_user_id("current")` 恒为 `session_current`，且问候持久化到 `append_messages(session_id=...)`。单一会话下这是自洽的（单用户），但 `greeting_for` 签名仍保留 `session_id` 参数，语义上是「为某会话写问候」，与单用户模型略有张力。

**影响**：功能正确，无 bug。属设计遗留，可择机简化签名。

---

### P2-4：`tasks.py` 的 `schedule` 中 `_inflight` 清理不彻底

**位置**：`backend/core/tasks.py:36-40`

```python
try:
    task = asyncio.get_running_loop().create_task(_runner())
    _tasks.add(task)
except RuntimeError:
    _inflight.discard(key)
```

`create_task` 理论上只会抛 `RuntimeError`（loop 已关闭），已捕获。但若未来出现其他异常类型，`_inflight` 里的 key 会残留，导致该 key 永久跳过。属理论隐患。

**影响**：极低，当前不可触发。

---

## 四、P3 级问题（低 / 可忽略）

### P3-1：`llm.py` 的 `get_perception_client` 用函数属性做缓存

**位置**：`backend/core/llm.py:74,92`

```python
pc = getattr(get_perception_client, "_client", None)
...
get_perception_client._client = pc
```

缓存挂在函数对象属性上，而非模块级变量。功能正确，但：
- 与 `get_client` 的 `global _client` 风格不一致
- reload 场景下（config 变更）不会自动失效

**影响**：无功能 bug，纯风格问题。已在之前会话评估过，不改。

---

### P3-2：`pipeline.py` 的 `apply_impulse` 未传 `reply`

**位置**：`backend/core/pipeline.py:450-458`

```python
apply_impulse(user_id, ..., text=text)  # 没传 reply=reply
```

`reply` 是事件级记忆的预留字段，`_event_line` 当前只用 `text`，不影响功能。已在之前会话确认，保留现状。

---

### P3-3：`app.py` 的 `_spawn_bg` 定义在 `_startup` 之后

**位置**：`backend/app.py:250-256`（定义）在 `_startup`（178-248）之后。

Python 函数体内名字在调用时解析，`_startup` 是 startup 回调、运行时 `_spawn_bg` 已定义，故**不是 bug**。纯可读性瑕疵。

---

## 五、审查过程中确认「非 bug」的关键点

| 疑点 | 结论 |
|------|------|
| 单一会话下 `_user_id` 恒为 `session_current`，多用户隔离是否失效？ | 桌面端单用户设计，自洽，非 bug |
| `_spawn_bg` 定义在 `_startup` 之后 | 运行时才解析，非 bug |
| 辱骂词库 `shabi` 重复出现两次 | `_ABUSE_ALIASES` 里 `"shabi"` 写了两遍，去重后无影响 |
| SSE + 主进程轮询双通道 | 已有 `notifiedKey` / `lastNotifiedText` 双去重，非 bug |
| `session_store.init()` 在模块导入时 + startup 各调一次 | 幂等（`INSERT OR IGNORE` + `IF NOT EXISTS`），非 bug |

---

## 六、建议的修复优先级

1. **P1-1**（删死代码）→ 5 分钟，零风险，立即做
2. **P1-3**（退出归档时序）→ 需理顺 `stopBackend`/`archiveSessionOnQuit`/托盘退出的先后，中风险
3. **P1-2**（initiative 裸访 conn 加锁）→ 一行改动，低风险
4. **P2-2**（清理多会话遗留 CRUD）→ 需确认引用，建议下个迭代统一处理

---

*审查时间：2026-09-02*
