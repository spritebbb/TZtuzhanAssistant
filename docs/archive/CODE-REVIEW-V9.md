# 代码审查报告 V9（系统性审查 + Bug 定位）

> 审查范围：后端核心层 + 高危插件（code_exec / system / external）+ 工具安全/确认/审计 + 会话存储
> 审查方式：逐文件通读 + 实际复现验证（非仅静态阅读）
> 审查人：代码审查专家 · 火眼眼
> 日期：2026-09-03

---

## 一、结论速览

| 级别 | 数量 | 说明 |
|------|------|------|
| 🔴 P0（崩溃/安全） | 0 | 无崩溃级、无可直接利用的安全漏洞 |
| 🟠 P1（高，建议尽快修） | 2 | 一个真实功能 bug + 一个高危能力缺口 |
| 🟡 P2（中，择机修） | 4 | 健壮性 / 性能 / 一致性 |
| 🔵 P3（低，可忽略） | 2 | 死代码 / 风格 / 理论隐患 |

**总体评价**：项目代码质量**整体扎实**，安全设计尤其用心（SSRF 防护、路径白名单、命令黑名单、来源 IP 鉴权、审计日志、每步确认、沙箱双保险）。真正的 bug 集中在少数边界，且都不是"一触即炸"的严重问题。

---

## 二、P1 级问题（建议尽快修）

### P1-1：`run_python` 沙箱的 `exec` 传了分离的 globals/locals，导致函数体无法引用模块级变量（真实功能 bug）

**位置**：`plugins/code_exec.py:131`（`_RUN_CHILD_SRC` 字符串内）

```python
exec(code, {'__builtins__': safe}, {})   # ← globals 和 locals 是两个不同的 dict
```

**问题**：Python 的 `exec(code, globals, locals)` 当两者是**不同对象**时，模块顶层的赋值（`x = 1`）落入 `locals`，而函数体运行时只从 `globals` 解析名字——于是：

```python
x = 1
def f():
    print(x)      # NameError: name 'x' is not defined
f()
```

**实际复现结果**：`NameError: name 'x' is not defined`（已用最小脚本验证）。

**影响**：用户在 `run_python` 里写**任何带函数的正常代码**（定义辅助函数后调用，函数里引用模块级变量/常量/之前定义的函数），都会莫名报 `NameError`，体验极差且难以排查。

**建议**：把 globals 和 locals 合并为同一个 dict：
```python
g = {'__builtins__': safe}
exec(code, g, g)   # 或直接 exec(code, g)
```

---

### P1-2：`run_python` 沙箱逃逸链存在"字符串拼接绕过 AST 静态扫描"的缺口（防御性缺陷）

**位置**：`plugins/code_exec.py:61-70`

```python
elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
    if len(node.args) >= 2:
        if isinstance(node.args[1], ast.Constant):   # ← 只拦常量
            ...拦截...
        else:
            return "（不允许动态 getattr（反射逃逸防护））"  # 非常量也拦
```

**问题**：`getattr(obj, "__" + "class__")` 这种**字符串拼接**写法，第二参数是 `ast.BinOp`（既不是 Constant，也……当前代码里 `else` 分支会拦"非 Constant"）。

**需澄清的结论**：我实际测试后发现，当前代码的 `else` 分支会拦截**所有非 Constant 的第二参数**，所以拼接写法**也会被拦**。真正的问题是另一处——**AST 扫描只拦截 `getattr` 这一条调用路径，但 `getattr` 本身就不在白名单 builtins 里**，因此这条逃逸链当前"鸡生蛋"式地无法成立。

**准确结论**：沙箱实际上**比预期更坚固**（白名单去掉了 `getattr`/`type`/`open`/`eval`/`exec`/`__import__`，AST 又拦了反射属性名）。但存在两个**防御性隐患**值得记录：

1. **`__build_class__` 在白名单里**：`class` 语句依赖它，但有了它就能用 `class` 构造任意对象，配合 `__init__`/`__globals__`（已被 AST 拦属性名）理论上仍有探索空间。属"防御纵深"，非当前可利用漏洞。
2. **黑名单式防护的固有脆弱性**：代码注释已诚实声明"不保证封死"，但**既然做了拦截，建议补一层运行时兜底**——把 `__build_class__` 从白名单移除，改为子进程内用受限的 class 创建，或至少明确"这是有限拦截"。

**建议**：本项**不是可利用漏洞**，但建议在代码注释里把"实际能力边界"写清楚（哪些能逃逸、哪些不能），避免后人误以为"完全沙箱化"而放松确认文案。真正的高危能力兜底仍靠 `needs_confirm=True` + 确认文案如实告知风险，这一点项目已做到。

---

## 三、P2 级问题（择机修）

### P2-1：`session/store.py` 每次操作新建连接且无 `busy_timeout`

**位置**：`backend/session/store.py:35-39`

```python
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB)
    conn.execute("PRAGMA journal_mode=WAL")   # 无 busy_timeout
```

**问题**：每个增删改查都 `_connect()` 新建连接再 `close()`，且未设 `PRAGMA busy_timeout`。当前被 `asyncio.Lock` 串行化，单进程下无并发问题。但：
- 每次建连 + 设 WAL 有固定开销，高频读写（每条消息都走这里）下是浪费。
- 一旦未来引入多进程（Electron 主进程也碰 sessions.db？）或多处未加锁访问，会撞 `database is locked`。

**建议**：参考 `userdb.py` 的做法——单一长连接 + `check_same_thread=False` + `busy_timeout=5000`，读写统一走锁。

### P2-2：`userdb.py` 的 `update_task` 用 f-string 拼 SQL（虽字段来自白名单）

**位置**：`backend/core/userdb.py:303-307`

```python
set_clause = ", ".join(f"{k}=?" for k in updates)
self.conn.execute(f"UPDATE tasks SET {set_clause} WHERE user_id=? AND id=?", vals)
```

**问题**：`updates` 的键来自 `allowed` 白名单集合（`{"content","status","phase","priority","progress","blocked_reason"}`），所以**不是 SQL 注入**。但 f-string 拼 SQL 是"坏味道"，一旦未来白名单逻辑被改动（比如把用户可控字段加进 allowed），就会打开注入口。

**建议**：虽然当前安全，但建议改成显式列名（或用 `sqlite3` 的参数化 + 固定列），消除"看起来像注入"的隐患，也给静态扫描工具（bandit）一个安静。

### P2-3：`userdb.py` 的 `reset()` 清表列表漏了 `tasks` 表

**位置**：`backend/core/userdb.py:825-830`

```python
for table in ("affection_log", "long_memory", "facts", "user_meta", "messages",
              "users", "kv_store", "important_dates", "stickers",
              "user_profile", "user_terms", "user_style_map", "diary", "triples"):
    self.conn.execute(f"DELETE FROM {table}")
```

**问题**：`_SCHEMA` 里定义了 `tasks` 表（第 136-148 行），但这个"文件删除失败时退化清空"的路径**漏掉了 `tasks`**。意味着：数据库文件被占用无法删除时，`reset()` 清空后 `tasks` 表里的旧任务会残留，导致测试/重置后出现"幽灵任务"。

**影响**：仅在 `reset()` 且文件被占用（`PermissionError`）时触发，概率低，但一旦触发就是数据不一致。

**建议**：把 `"tasks"` 加进清表列表。

### P2-4：`_exec_in_subprocess` 用 `int` 超时但子进程可能残留孙进程

**位置**：`plugins/code_exec.py:101-105`

```python
except asyncio.TimeoutError:
    proc.kill()          # 只杀直接子进程，不杀孙进程
    await proc.wait()
```

**问题**：`run_python` 的子进程里如果用户代码 `spawn` 了孙进程（虽然 `subprocess`/`multiprocessing`/`os` 都被禁，但通过已导入的模块间接拉起仍可能），`proc.kill()` 只杀直接子进程，孙进程可能残留成孤儿。同理 `_run_command`、`external.py` 的 `_codex_run`/`_dsh_run` 超时 kill 也有此问题。

**影响**：低概率（沙箱已禁 `subprocess`/`os`），但长跑后可能累积僵尸进程。

**建议**：Windows 下用 `taskkill /T`（带子进程树）或 `psutil` 的 `kill(children=True)` 兜底。

---

## 四、P3 级问题（低 / 可忽略）

### P3-1：`chat.py` 里 `_cb`/`_image_cb` 推队列无背压，理论可无限堆积

**位置**：`backend/api/chat.py:58-70` + `q.put()`

`process` 流式回调高频 `await q.put(piece)`，若前端 SSE 消费慢，`asyncio.Queue`（无 maxsize）会无限堆积。已由 `_PROCESS_TOTAL_TIMEOUT=300` 兜底，但堆积本身无上限。

**建议**：给 `q = asyncio.Queue(maxsize=...)` 设上限，或 `put_nowait` + 丢弃策略。属理论优化。

### P3-2：`audit.py` 的 `_result_summary` 对 None 输出处理

**位置**：`backend/tools/audit.py:50-53`

```python
s = output or ""
s = s.replace("\n", " ")   # 若 output 是非 str 会抛，但调用方都传 str，安全
```

已由 `result.output`（str）保证，无实际 bug。仅防御性提示。

---

## 五、审查过程中确认「非 bug」的关键点（避免误报）

| 疑点 | 结论 |
|------|------|
| `run_python` 沙箱能否被字符串拼接逃逸？ | **实测不能**——`getattr` 本身不在白名单 builtins，且 AST 的 `else` 分支会拦非 Constant 第二参数。沙箱比预期坚固 |
| `session/store.py` 的 `asyncio.Lock` + `to_thread` 是否会并发写？ | 不会——`async with _lock` 的 acquire/release 都在事件循环线程，`to_thread` 期间锁持有，串行化可靠 |
| `userdb.py` `update_task` 的 f-string 拼 SQL 是注入吗？ | 不是——字段名来自硬编码白名单，值走 `?` 参数化 |
| `external.py` 的 `codex_run` 是否越权执行命令？ | 有 `needs_confirm=True` + 确认文案明确警示"不受安全黑名单约束"，符合设计意图 |
| `confirm.py` 钩子异常会误放行吗？ | 不会——危险类别（write/run/external）异常时**默认拒绝**，只读才放行 |
| `safety.py` 的 SSRF 防护是否覆盖 DNS 重绑定？ | 是——`check_url` 对每个解析结果都判内网，且注释要求调用方对重定向复检 |

---

## 六、修复优先级建议

| 优先级 | 项 | 工作量 | 风险 |
|--------|-----|--------|------|
| 1 | **P1-1** `exec` globals/locals 合并（修复函数体 NameError） | 1 行 | 极低 |
| 2 | **P2-3** `reset()` 清表补 `tasks` | 1 行 | 极低 |
| 3 | **P2-1** `store.py` 改长连接 + busy_timeout | 中 | 低 |
| 4 | **P2-2** `update_task` 消除 f-string 拼 SQL | 小 | 低 |
| 5 | **P1-2** 沙箱注释写清能力边界（非漏洞，文档性） | 小 | 无 |
| 6 | **P2-4** 子进程超时 kill 补孙进程清理 | 中 | 低 |

**建议**：P1-1 和 P2-3 各 1 行改动，零风险，可立即提交；其余登记 backlog 随迭代处理。

---

*审查遵循《代码审查标准与流程》docs/CODE-REVIEW-STANDARD.md。*
