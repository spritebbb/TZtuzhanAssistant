# 代码审查报告 V10 —— 第二轮深入审查

> 审查人：代码审查专家（火眼眼）
> 日期：2026-09-03
> 范围：backend/api、backend/core、backend/tools、backend/session、plugins（全量）
> 结论：**P0=0、P1=2、P2=4、P3=3**

---

## 一、总体印象

这轮我把第一轮没覆盖到的**工具循环、SSE 流式、Agent 长任务、数据层、文件/网页/外部桥插件**全量过了一遍。整体判断不变：**代码质量扎实，安全设计是全项目最用心的部分**——SSRF 逐跳复检、路径白名单、命令语义黑名单、来源 IP 鉴权、`secrets.compare_digest` 防时序攻击、`copy-on-write` 工具注册表，这些细节都做到了位，且大部分位置都写了清晰的「为什么」注释。

本轮新发现的问题集中在**并发/生命周期**这类容易在代码审查里漏网、但线上偶发的 bug，以及**关键词不一致导致的死代码**。

---

## 二、本轮新发现的 Bug

### 🟠 P1-1：SSE 后台任务无强引用，可能被 GC 静默取消

**位置**：`backend/api/chat.py` 第 118 行

```python
async def sse() -> AsyncGenerator[str, None]:
    task = asyncio.create_task(_runner())
    try:
        ...
    finally:
        pass   # 注释说"不 cancel 任务"，但也没持有引用
```

**问题**：`task` 是局部变量，`finally` 里既没有 `await task`，也没有把 task 存入任何强引用集合。Python 官方文档对 `asyncio.create_task` 明确警告：

> "Save a reference to the result of this function, to avoid a task disappearing mid-execution."

**影响**：`_runner()` 负责持久化 bot 回复、补存超时/错误消息。如果 Task 被 GC 回收（事件循环只对「有 pending callback」的 task 保活，跨多个 `await` 点后可能失去这个保证），`_runner` 会被静默 `CancelledError` 取消，导致：
- 用户看到流式回复，但回复**没有落库**（sessions 表里只有 user 消息、缺 bot 消息）；
- 后续刷新页面，这条回复「消失」了。

**对比佐证**：项目其他两处都做对了——`pipeline.py` 用 `_memory_tasks` 集合强引用、`agent.py` 用 `_agent_bg_tasks` 集合强引用。唯独 `chat.py` 漏了。

**建议**：
```python
_BG_TASKS: set[asyncio.Task] = set()   # 模块级

task = asyncio.create_task(_runner())
_BG_TASKS.add(task)
task.add_done_callback(_BG_TASKS.discard)
```
或在 `finally` 里显式 `await task`（但这会阻塞 SSE 关闭，不如强引用集合好）。

---

### 🟠 P1-2：天气查询分支是死代码（关键词不一致）

**位置**：`backend/core/pipeline.py`

- 第 184 行 `_SEARCH_KEYS`：`('搜索', '搜一下', '查一下', '帮我查', '查查', '新闻', '天气', '多少钱', '价格', '汇率', '现在几点', '最新', '今天有', '今天有没有')`
- 第 611 行：`if not mock and _needs_search(text):` —— 这是整个搜索块（含天气）的**总开关**
- 第 615 行天气判断：`if any(k in text for k in ("天气", "温度", "冷", "热", "下雨", "气温", "天气预报")):`

**问题**：天气分支里专门处理了 `"冷"、"热"、"下雨"、"温度"、"气温"`，但这些词**不在** `_SEARCH_KEYS` 里。`_needs_search` 返回 `False` 时，第 611 行的整个搜索块（包括天气查询）根本不会执行。

**实测验证**（脚本复现）：
| 用户输入 | `_needs_search` | 天气分支 | 实际效果 |
|---------|----------------|---------|---------|
| 今天好冷啊 | ❌ False | ✅ True | **天气不查** |
| 今天下雨吗 | ❌ False | ✅ True | **天气不查** |
| 好热啊今天 | ❌ False | ✅ True | **天气不查** |
| 今天天气怎么样 | ✅ True | ✅ True | 正常查 |
| 今天多少度 | ❌ False | ❌ False | 都不查 |

**影响**：「今天好冷/好热/下雨吗」这类极常见的天气问法，走的是 `MOOD_CITY` 真实天气查询的设计意图，但因为总开关没放行，实际**永远不会触发**，LLM 只能瞎猜或说不知道。

**建议**：把天气专属词补进 `_SEARCH_KEYS`（或让天气判断独立于 `_needs_search` 先执行）。最小改动：在 `_SEARCH_KEYS` 里加 `"冷", "热", "下雨", "温度", "气温"`。

---

### 🟡 P2-1：`session/store.py` 每次操作新建连接，无跨连接写锁

**位置**：`backend/session/store.py`

`_connect()` 每次 `get_messages`/`append_messages` 都 `sqlite3.connect(_DB)` 新建连接、用完即关。串行写靠 `asyncio.Lock` 保证（单事件循环内可靠），但：

1. **每次建连 + `PRAGMA journal_mode=WAL`** 有固定开销（WAL 是数据库级持久属性，重复设置意义不大）；
2. `_connect()` **没有 `busy_timeout`**（对比 `userdb.py` 里 `busy_timeout=5000`）。一旦未来有多进程/多 worker 部署，或锁被绕过，会直接撞 `database is locked` 异常而非等待。

**建议**：`_connect()` 加 `PRAGMA busy_timeout=5000`；可考虑模块级长连接 + `check_same_thread=False`（与 `userdb.py` 一致）。

---

### 🟡 P2-2：`update_task` 用 f-string 拼 SQL（当前安全，属坏味道）

**位置**：`backend/core/userdb.py` 第 303-307 行

```python
set_clause = ", ".join(f"{k}=?" for k in updates)
self.conn.execute(f"UPDATE tasks SET {set_clause} WHERE user_id=? AND id=?", vals)
```

**评估**：字段名来自 `allowed` 白名单（`content/status/phase/priority/progress/blocked_reason/updated_at/completed_at`），值全走 `?` 占位符，**当前无 SQL 注入风险**。但这是「用字符串拼 SQL 结构」的坏味道，未来若有人往 `allowed` 里加字段、或字段名来源失控，容易翻车。

**建议**：白名单拼字段名是可接受的做法（SQL 无法参数化列名），但建议加一句注释说明「字段名必须来自 `allowed` 白名单，禁止接受外部输入」，并在 CI 里用 bandit 扫描兜底。

---

### 🟡 P2-3：`chat_stream` 重试后 `produced` 标志无残留问题，但缺「首块前失败」的流式重试一致性

**位置**：`backend/core/llm.py` 第 205-250 行

逻辑本身是对的（`produced` 在循环体开头重新初始化，已 yield 过就 `break` 不重试，避免前端重复文本）。但存在一个**未被测试覆盖的边界**：

- `stream=True` 的连接建立后、`async for chunk in stream` 尚未产出任何 `piece` 时就抛异常 → `produced=False`，会重试。
- 但重试时**从头重新创建 stream**，如果此时前端已经收到了前面几轮的 `piece`（极端：第一轮 yield 了一个空内容 chunk 后失败），会有轻微不一致。这个概率极低，且注释已说明权衡，**记为 nit 即可**。

---

### 🟡 P2-4：`_run_python` 沙箱的「诚实声明」与「静态扫描」之间仍有防御缺口（已知，非可利用）

**位置**：`plugins/code_exec.py`

第一轮已详细分析：白名单去掉了 `getattr`/`type`/`open`/`eval`/`exec`/`__import__`，AST 拦截了 `__class__` 等反射属性。字符串拼接 `getattr(type(1), "__"+"class__")` 虽然能绕过 AST 的 `getattr` 常量检查，但 `getattr` 本身不在白名单里，形成「鸡生蛋」死锁，**当前不可利用**。

本轮补充一个更精确的观察：`_RUN_CHILD_SRC` 里的白名单**保留了 `__build_class__` 和 `isinstance`/`issubclass`**。这三者组合 + 用户代码里自定义的 class，理论上能构造出带 `__globals__` 的函数对象。但由于 AST 已经把 `__globals__`/`__code__`/`__init__` 等属性名列为 `_FORBIDDEN_ATTRS`，直接访问会被拦。**结论维持：防御性缺口，非可利用漏洞，但既然声明了「非隔离沙箱」，建议在文档里把这句话加粗，避免用户误以为真的隔离。**

---

### 💭 P3-1：`_needs_tool_loop` 的触发词与 `_TOOL_LOOP_KEYS` 存在「查」单字误伤

**位置**：`backend/core/pipeline.py` 第 198 行

`_TOOL_LOOP_KEYS` 末尾有单独的 `"查"` 和 `"记"` 类单字词（第 198 行 `"查"`）。用户说「我查查」「查一下」还好，但「你不懂查证一下」「查寝」这种含「查」字的闲聊也会误触发工具循环，多一次 Function Calling 往返。

**建议**：单字触发词建议加边界判断，或直接去掉裸 `"查"`（已有「查一下」「查查」等更精确的词覆盖）。

---

### 💭 P3-2：`agent.py` 的 `_task_channels` 用 `dict` + 延迟删除，无上限保护

**位置**：`backend/api/agent.py` 第 22 行

`_task_channels` 靠 `_drop_channel_later` 延迟 300s 删除。但 `_drop_channel_later` 的 `asyncio.sleep` 任务本身**没有强引用**（第 168 行 `asyncio.create_task(_drop_channel_later(task_id))`），同样有被 GC 回收的风险——如果被回收，对应 channel 永远不会清理，`_task_channels` 会缓慢增长（每个 task_id 一条，直到进程重启）。

**建议**：与 P1-1 同款修复，给 `_drop_channel_later` 的 task 加强引用，或改用 `asyncio.ensure_future` 存集合。

---

### 💭 P3-3：`external.py` 硬编码 `D:\DSH\deepseek-harness` 和 `D:\DSH` 路径

**位置**：`plugins/external.py` 第 30-34 行

```python
_DSH_CLI_DEFAULT = Path(r"D:\DSH\deepseek-harness\apps\cli\lib\bin.js")
_DSH_CWD = os.getenv("DSH_CWD", r"D:\DSH")
```

Codex 路径有自动探测（`rglob("codex.exe")` + 环境变量覆盖），做得很好；但 DSH 的默认路径是**硬编码绝对路径**，换机器/换目录会失效。虽然提供了 `DSH_CWD` 环境变量和 `config.agent_dsh_cli` 覆盖，但默认值硬编码仍是隐患。

**建议**：DSH 路径也做「环境变量 → 默认路径探测 → PATH `shutil.which`」的探测链（Codex 的 `_codex_path` 已是好模板）。

---

## 三、值得表扬的地方（本轮新观察）

1. **`ToolRegistry` 的 copy-on-write 并发设计**（`base.py`）：写操作锁内复制 dict 替换、读操作锁内取引用快照后立即释放，注释把「为什么不用深拷贝、为什么不阻塞读」讲得很清楚。这是教科书级的并发正确性。
2. **`confirm.py` 的「钩子异常 → deny」兜底**：确认钩子内部抛异常时，危险类操作一律拒绝、只读类放行，且注释点明「若返回 allow 会吞掉 base.py 的兜底」，防御层次严密。
3. **`tool_loop.py` 的 `_clean_args` 参数清洗层**：专门处理 DeepSeek 系模型「把整句指令塞进单个参数」的毛病，剥离指令前缀、路径归一化、自然语言算式转 Python，还区分了「正文类参数禁止裁剪句尾语气词」（避免误删「我家的狗很乖的」的「的」）。这种对 LLM 行为边界的理解非常到位。
4. **`web_fetch.py` 的逐跳重定向复检**：`_NoRedirect` 让每次 3xx 都显式 `check_url` 复检，防 302 跳内网 SSRF，比「一次性检查」更严谨。
5. **`llm.py` 的流式重试「produced」守卫**：已经 yield 过内容就绝不重试，避免前端收到重复文本——这个细节很多生产代码都会漏。

---

## 四、修复优先级建议

| 优先级 | 问题 | 修复成本 | 建议动作 |
|-------|------|---------|---------|
| 🔴 P1-1 | SSE 任务无强引用 | 低（3 行） | 立即修复 |
| 🟠 P1-2 | 天气分支死代码 | 低（1 行） | 立即修复 |
| 🟡 P2-1 | store 缺 busy_timeout | 低（1 行） | 顺手修 |
| 🟡 P2-2 | update_task f-string | 低（加注释） | 顺手修 |
| 🟡 P2-4 | 沙箱声明 | 低（改文档） | 顺手修 |
| 💭 P3-1/P3-2/P3-3 | 误伤/硬编码/引用 | 低 | 后续迭代 |

**其中 P1-1 和 P1-2 是这轮最该优先处理的**：一个是「回复偶尔不落库」的偶发数据丢失，一个是「常见天气问法永远不触发真实查询」的功能失效，都是用户可感知的问题。

---

## 五、下一步

是否要我立即修复 **P1-1（SSE 任务强引用）** 和 **P1-2（天气关键词）** 这两个零风险、用户可感知的 bug？其余 P2/P3 可一并处理或按你指定的编号逐项来。
