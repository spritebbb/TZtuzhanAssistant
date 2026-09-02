# 菟菚桌面助手（TZtuzhanAssistant）代码审查报告

- 审查范围：`backend/`（全部核心链路）+ `frontend/`（src + electron），不含 `_legacy/`
- 审查方式：主审精读约 35 个核心文件（工具层/安全层/授权层/API 层/会话层/记忆引擎/pipeline/llm/userdb/前端 SSE 与渲染），内置工具目录由并行审查完成；交叉验证了关键疑点（时序、调用链、schema）
- 分级：P0 = 可被利用或必然出错；P1 = 特定条件下出错；P2 = 可疑/边缘/设计缺陷

---

## 一、P0：可被利用的安全问题

### P0-1 「无确认执行」攻击链：CORS 全开 + 关键端点无鉴权 + 无通道时确认钩子直接放行

三个独立弱点叠加成一条完整可利用链：

1. `backend/app.py:37-43` — `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])`
2. `backend/tools/mcp_server.py:55-81` — `POST /mcp/call` 完全无鉴权，可直接调 `ToolRegistry.execute`（含 run_command / write_file）
3. `backend/api/remote.py:30-34` — `AGENT_REMOTE_TOKEN` 默认为空时 `_token_ok` 恒 True（"本地信任"）
4. `backend/tools/confirm.py:148-151` — `default_confirm_hook` 在 `current_sse_push` 为 None（无前端 SSE 通道）时**直接 return "allow"**

**触发路径**：用户浏览器里打开任意恶意网页 → 网页 `fetch("http://127.0.0.1:8801/mcp/call", {method:"POST", ...})`（CORS 全开，preflight 直接通过）→ `run_command` 经 `ToolRegistry.execute` → 无 SSE 上下文 → 不弹确认 → **静默执行系统命令**（黑名单只拦少量模式，见 P1-8）。`/api/remote/task` 同理。

**修复建议**：
- 校验 `Origin`/`Host` 头：仅允许 `file://`、`localhost`、Electron 自身来源；或干脆去掉 `allow_origins=["*"]`
- `/mcp/call`、`/api/remote/*`、`POST /api/config`（见 P2-6）强制要求 token（默认生成并持久化，而不是空=放行）
- `default_confirm_hook` 在无 SSE 通道时对 `write/run/external` 类工具默认 **deny**（或至少读环境标记区分"本机 MCP 调用"与"未知来源"）

### P0-2 `web_fetch`：`file://` 读取任意本地文件 + SSRF，且为 read 类自动执行

`backend/tools/builtin/web_fetch.py:16-18`：URL 无 scheme/IP 限制，`urlopen` 支持 `file://`；该工具注册为 `category="read"`（`needs_confirm=False`），**不弹确认直接执行**。
- `web_fetch("file:///C:/Users/<user>/.ssh/id_rsa")` → 私钥等本地文件内容直接进入 LLM 上下文（并可被后续工具外传）
- `web_fetch("http://169.254.169.254/...")` / 内网地址 → SSRF

**修复建议**：scheme 白名单（http/https）+ 内网/环回/链路本地地址段拦截 + `resp.read(N)` 大小上限 + `asyncio.to_thread` 包裹。

### P0-3 `run_python` 沙箱可被经典逃逸绕过

`backend/tools/builtin/code_exec.py:41-45`：只 pop 掉 `__import__/exec/eval/open` 等 builtins、AST 只挡 import 语句。`().__class__.__bases__[0].__subclasses__()` 链 100% 可拿到 `os/subprocess`。确认框只显示"代码 N 字符"，用户无法审阅内容。
**修复建议**：文档明确沙箱非安全边界；确认框展示代码摘要（前 N 行）；或改为受控子进程（临时目录 + 超时 + 资源限制）。

---

## 二、P1：特定条件下必然出错

### P1-1 pipeline 时序 bug：先存档后判"跨场"，导致三个惰性特性全部死代码

`backend/core/pipeline.py`：
- L321：`db.add_message(user_id, "user", text)`（用户消息先存档，ts=now）
- L381、L391、L520：`_long_gap(db.last_message_ts(user_id))` —— 此时最后一条消息就是刚存档的这条，`last_message_ts` 返回现在（`userdb.py:712-716` 按 id 倒序取最新）

结果：`(now - now).total_seconds() < 30min` 恒成立 → `_long_gap` 恒 False：
- 1.7 惰性话题提炼（L377-384）**永不触发**
- 1.8 五元组提取（L386-394）**永不触发**
- 4.0.2 跨会话话题延续注入（L517-539）**永不生效**（"上次聊到哪"功能失效）

**修复建议**：在 1.0 存档**之前**先取 `prev_ts = db.last_message_ts(user_id)`，用 `prev_ts` 做空闲判定。

### P1-2 流式回复是死代码：生产路径永远不流式

`backend/core/pipeline.py:804` `use_tool_loop = not mock` → 生产环境必走 `run_tool_round`（L854-860），返回完整文本；L867-877 的 `chat_stream` 流式分支只在 `use_tool_loop=False 且 not mock` 时可达 —— 条件互斥，**死代码**。SSE 只会发 `done` 帧，打字机效果从未生效；而"重复检测重写"路径（L906-918）却会流式，行为不一致（用户先看到长时间空白、重写时突然逐字输出）。
**修复建议**：工具循环模式下，对最终文本再走一遍 `chat_stream` 或推送分段回调；或明确接受整句模式并删掉死代码。

### P1-3 多轮工具调用 tool_call_id 重复

`backend/tools/tool_loop.py:409`：每轮循环 `c["_id"] = f"call_{i}"` 从 0 重编。第 2 轮后，消息历史中存在两条 assistant 消息都带 `tool_calls id=call_0`。OpenAI 官方端点容忍，但 DeepSeek/vLLM 等严格校验端点可能报 400。
**修复建议**：id 加轮次前缀，如 `f"call_{loop_count}_{i}"`（或沿用端点返回的真实 `tc.id` —— `llm.py:136-141` 目前把它丢了）。

### P1-4 确认钩子异常 = 未经确认执行

`backend/tools/base.py:209-211`：`except Exception: confirmed = "allow"`（注释："钩子异常：保守放行"）。前端断连导致 `push` 抛错时，本该弹确认的写/命令操作**直接执行**。对安全钩子，异常时应默认 deny。
**修复建议**：改为 `confirmed = "deny"`（或至少对 `CATEGORY_RUN/WRITE` deny）。

### P1-5 记忆引擎存量迁移静默失效（跨线程误用事件循环）

`backend/core/memory/engine.py:33-39`：`ensure_ready()` 由 `app.py:69` 的 `await asyncio.to_thread(ensure_ready)` 在**工作线程**里执行，其中 `asyncio.get_event_loop()` 在无事件循环的非主线程会抛 RuntimeError（Python 3.10+）→ 被 `except` 吞掉 → "检测到存量记忆，开始迁移"分支**从不执行**。
**修复建议**：`ensure_ready` 不做事件循环判断，把"是否需要迁移"的判断与调度整体移回 `app.py` 的 startup 协程（`asyncio.create_task(asyncio.to_thread(migrate))`）。

### P1-6 `db.conn`（全局单连接）跨线程并发使用

`backend/core/userdb.py:164`：`sqlite3.connect(..., check_same_thread=False)` 单例连接、无锁。绝大部分 DB 调用在事件循环线程没问题，但：
- `engine.py:111-121` `startup_backfill` 把 `db.conn` 的查询放进 `asyncio.to_thread(_scan_and_index)` 工作线程，与事件循环线程的 pipeline 写入并发 → 同一连接跨线程并发使用可能触发 `sqlite3.ProgrammingError`（递归/交错游标）或数据错乱
- 未来任何 `to_thread(db.xxx)` 都会踩中

**修复建议**：给 `UserDB` 加 `threading.RLock` 包住全部 execute；或工作线程路径一律走独立连接。

### P1-7 embedding 回退链维度混存 + 每次失败重试加载

`backend/core/memory/embedding.py`：
- bge-m3=1024 维、bge-small/哈希=768 维。模型加载失败后 `_hash_vec`（768 维）与后续加载成功的 1024 维向量**混存同一 Chroma collection** → 查询时 HNSW 维度不匹配报错 → 向量检索整体降级（`search` 返回 []）
- `dim()` 用 `lru_cache(1)`：首次探测若发生在模型未就绪时，固定返回 768，之后模型成功也沿用错值
- `_load_model()` 失败后 `_model=None` 但**没有"已尝试失败"标记** → 每条新文本 embed 都会重新尝试下载模型（阻塞 to_thread 线程数秒~数十秒）
**修复建议**：记录"当前生效模型名"作为 collection 元数据，模型变化时重建/分库；失败后设冷却期（如 10 分钟内不再重试）；`dim()` 改为跟随 `_model_name`。

### P1-8 危险命令黑名单可绕过

`backend/tools/safety.py:63-84` 与 `config.py:92-97` 默认值：
- `rmdir /s /q D:\x` —— `rm\s+-rf` 不匹配（`rm` 后是 `d`）、`rd\s+/s` 不匹配（`rd` 后是 `ir`）→ **Windows 等价于 rm -rf 的命令直接放行**
- `del /q /f x`（顺序颠倒）不匹配 `del\s+/f`
- `Remove-Item -r -Force`（`-r` 别名）不匹配 `.*-Recurse`
- `open_app`（`builtin/system.py`）不经 `check_command`，`open_app(command="cmd /c rd /s /q D:\project")` 可绕过 run_command 的黑名单
**修复建议**：补 `rmdir`、`rm -r`、`Remove-Item\s+-r\b`、`del\s+/.*q.*f` 等模式；`open_app`/`browser_open` 执行前统一过 `check_command`。

### P1-9 子进程超时后不 kill（孤儿进程）

`backend/tools/builtin/code_exec.py` 与 `external.py`（codex_run/dsh_run）：`asyncio.wait_for(proc.communicate(), timeout=...)` 超时只取消 await，**子进程继续运行**（`ping -t`、挂起的 codex 会话等）。
**修复建议**：`except asyncio.TimeoutError: proc.kill(); await proc.wait()`。

### P1-10 `/api/remote/task` 实参形式与文档不符

`backend/api/remote.py:38`：`task: str = ""`、`token: str = ""` 是**query 参数**（无 `Form(...)`/Body 注解），但 docstring 与交付文档均写 "POST body: task"。以 JSON body 调用（DSH 集成文档的做法）会得到 400 "缺少 task"。另外 `GET /api/remote/task/{id}` **无 token 校验**，任务结果（可能含本机文件内容）可被任意本地进程/网页读取。
**修复建议**：改 `task: str = Form("")`（或同时支持 JSON body）；结果查询加同样的 token 校验。

### P1-11 前端：快速切换会话的竞态导致串会话显示

`frontend/src/components/ChatView.vue:29-36, 162-172`：`loadMessages` 开头同步设 `curSessionId`，随后 `await getMessages(id)`。快速 A→B→A 切换时两个请求并发，**后返回者覆盖 `messages`**——若 B 的响应后到，界面显示 B 的消息但当前会话是 A。发送消息同样存在 `botIndex` 指向已被清空数组的旧索引风险。
**修复建议**：`loadMessages` 里 await 后校验 `id === curSessionId` 再赋值（或用请求序号守卫）；切换会话时同时 abort 进行中的流（已有）并禁用发送直到加载完成。

---

## 三、P2：可疑 / 边缘 / 设计缺陷

| # | 位置 | 问题 |
|---|------|------|
| 1 | `tools/safety.py:38-57` | `check_path` 用字符串前缀比较，Windows 下未存在路径 `resolve()` 不归一大小写 → `d:\dsh\...` 被误拒（反之 `rmdir` 类绕过见 P1-8） |
| 2 | `tools/confirm.py:74-78` | 超时判定与用户点击存在小竞态（超时瞬间点击 allow 仍按 deny） |
| 3 | `tools/tool_loop.py:217-293` | `_clean_args` 启发式改写 `command/code` 参数（剥前缀/去尾词），可能破坏合法命令参数 |
| 4 | `tools/tool_loop.py:397-400` | `call_native` 异常被吞，返回"（我先记一下，回头跟你说）"——真实错误（如 key 失效）被误导性文案掩盖 |
| 5 | `core/llm.py:138-141` | tool_calls 的 `arguments` JSON 解析失败静默变 `{}`，配合 `_fill_missing_args` 会把整句用户消息塞进工具参数 |
| 6 | `api/config_api.py` + `core/config.py:130-150` | `POST /api/config` 无鉴权（配合 CORS 全开，网页可改 LLM_BASE_URL 指向外部）；`update_env_file` 的 value 未过滤中间换行，理论上可注入伪 KEY 行 |
| 7 | `core/pipeline.py:931-932` | 每条消息把"用户说/菟菚说"双写 `long_memory`，表无限增长（无 TTL/上限清理） |
| 8 | `core/memory/compress.py:100-195` | 压缩在对话关键路径同步 `await`（触发时首字延迟+数秒）；LLM 摘要失败时 cursor 不推进 → 后续每条消息都重试一次 LLM 压缩 |
| 9 | `core/memory/compress.py:186-188` | 摘要写 Chroma 固定 `record_id=0`（靠 upsert 覆盖，仅保留最新一条，属有意但脆弱） |
| 10 | `tools/audit.py:93-110` | 查询时全量读入 `tool_log.jsonl`，无轮转，长期运行内存/IO 压力 |
| 11 | `api/chat.py:42-49` | 会话校验与 `append_messages` 之间会话可能已被删除；`append_messages` 返回 False 未检查（用户消息静默丢失） |
| 12 | `agent/session.py:227-228` | 计划步骤 `pending`（未确认）也会进入执行——"每步确认"只拦 denied；步骤级确认形同虚设（工具级确认仍有效） |
| 13 | `agent/session.py:30` | `TASK_TIMEOUT=300` 定义了但从未实现，长任务无总超时 |
| 14 | `tools/mcp_server.py:236-249` | 外部 MCP 工具默认 `category=read`、`needs_confirm=False`，远程工具无需确认即执行 |
| 15 | `plugins/loader.py:102-108` | ~~插件**覆盖**内置同名工具时，`after-before` 清点漏掉它 → 卸载插件后被覆盖的工具残留~~ **已修复**（插件系统 v2：加载前快照、卸载精确恢复，见 `docs/PLUGIN-SYSTEM-V2.md`） |
| 16 | `core/llm.py:152-157` | 降级判定 `str(exc)` 含 "tool"/"function" 即回退文本模式，判定过宽 |
| 17 | `api/remote.py:34` | token 用 `==` 明文比较（建议 `secrets.compare_digest`）；token 走 query 会进访问日志 |
| 18 | `frontend/ChatView.vue:115-117` | `resolveConfirm(allow, remember)` 的 `allow/remember` 形参未使用（仅移除面板项；实际 POST 在 ConfirmPanel 内完成，功能未断但接口误导） |
| 19 | `frontend/chat.ts:52` | `obj.done` 为空字符串时 falsy，会落空到 error 分支——当前后端有兜底文案，风险低 |
| 20 | `electron/main.ts:72-77` | `backendProcess.kill()` 强杀，后端无优雅关闭（备份/checkpoint 可能被打断） |
| 21 | `tools/builtin/file_search.py` | `async def _grep` 内全是同步 `os.walk`+逐行读+正则，大目录阻塞事件循环（应 `to_thread`） |
| 22 | `tools/builtin/subagent.py:69` | `agent_fanout` 对 `tasks_json` 无数量/并发上限，极端输入可打爆 API 限流 |
| 23 | `tools/builtin/web_fetch.py:22` | 无 `<body>` 标记时 `html[find(...):-1]` 截取错误 |
| 24 | `tools/builtin/todo.py` | `_todo_update` 状态值未 strip、`blocked_reason` 会覆盖已完成状态 |

---

## 四、做得好的地方

- `session/store.py`：每操作独立连接 + `asyncio.Lock` 串行 + LIKE 通配符转义 + 删除会话时孤儿图片清理，设计干净
- `frontend/utils/markdown.ts`：marked + DOMPurify，XSS 防护到位；Electron `contextIsolation:true` + preload 暴露面最小化
- `api/images.py`：文件名白名单正则 fullmatch，无路径遍历
- `maintenance/loop.py`：周期任务异常兜底、备份保留轮转、先 checkpoint 再备份的顺序正确
- `core/affection.py`：防刷屏窗口 + 每日奖励去重 + 单日扣分上限，规则引擎健壮
- `core/imagegen.py`：下载 20MB 上限、重试分类（网络重试/服务端不重试）、类型化错误

---

## 五、第二轮补充（core 模块并行审查，关键项已经主审交叉验证）

### P1 追加

**P1-12 `core/daily.py:173` — extract_facts 失败也推进游标，事实永久丢失**
LLM 重试耗尽后 `db.set_last_fact_msg_id(user_id, done)` 仍执行（注释写明"避免反复重试"），该批 ≤60 条消息中提炼出的事实**永不再被提取**。同项目 `profile.py:137-140` 是相反策略（失败不推进、保留重试）——两处不一致，daily 的策略造成数据永久丢失。
**修复**：与 profile 保持一致：失败不推进游标（后台任务有同 key 去重，不会打爆）。已验证 ✓

**P1-13 `core/llm.py:172-186` + `pipeline.py:871` — chat_stream 无重试，流式中断即整轮失败**
`chat`/`chat_native` 均有 3 次退避重试，唯 `chat_stream` 没有；pipeline 的 `async for` 外也无兜底。流式中途断网 → 本轮回复全丢（叠加 P2-11 孤立 user 消息）。
**修复**：create 调用包同款重试；`async for` 外层 try/except 兜底。

**P1-14 `core/affection.py:384-397` — 跨天补跑每日总结只补"昨天"，隔多天未聊时中间日子的总结永久丢失**
跨天回滚只调度 `run_daily_batch(uid, yesterday)` 一天。用户 9/1 聊天、9/5 再来：只补 09-04（多半无消息，直接 `set_batch_date(09-04)` 返回），**09-01 当日的好感度判定与事实提炼永不执行**，且 `last_batch_date` 被错误推进。已验证 ✓（`daily.py:98-100` 空日直接推进）。
**修复**：从 `last_batch_date`（或 `last_chat_date`）遍历到 `yesterday`，凡有消息且未总结的日子都调度。

### P2 追加

| # | 位置 | 问题 |
|---|------|------|
| 25 | `core/pipeline.py:321 vs 930` | 异常路径产生"孤立 user 消息"：user 先存档，assistant 回复在 930 行才存档，中间任何异常留下有问无答的孤立消息，污染后续 `short_term_messages`。建议异常时按 id 删除刚插的 user 消息或成对事务提交 |
| 26 | `core/mood.py:89/184/199` + `affection.py:351` | `_WEATHER_CACHE` 被 to_thread 子线程与主线程并发读写，无锁（读-改-写不原子） |
| 27 | `core/userdb.py:164/429-442` | 全部 db 方法在事件循环线程同步阻塞执行：`search_long_memory` 每查询遍历 500 行算 bigram、`_daily_penalty_total` 全表 LIKE——多会话并发时互相卡顿。慢查询应 `to_thread` |
| 28 | `core/pipeline.py:843 vs 896-905` | 重复回复重写时把 system 指令追加在 user 消息**之后**（与 388-391 行工具循环特意把 system 插到 user 前的正确做法相矛盾），部分端点会忽略或拒绝 |
| 29 | `core/intent.py:35-41` | `_DRAW_WORDS` 含"想看/让我看看/给我看看"——"给我看看你拍的截图"会误触发生图（与 `pipeline.py:491` 注释"只有 user 显式触发'画'才生成"的意图相悖）。已验证 ✓ |
| 30 | `core/userdb.py:828-838` | `delete_important_date(date_id, user_id=None)`：None 时无跨用户隔离删除 |
| 31 | `core/greeting.py:98-105` | `_set_last_seen` 先于 gap 判断执行且非原子，并发请求会重复生成久别问候 |
| 32 | `core/mood.py:32-35` | `MOOD_LEVELS`/`_WEATHER_BASE` 在模块 import 时固化，改 `mood_rules.json` 不生效（与热改预期矛盾） |
| 33 | `core/pipeline.py:188/216` | `strip_actions` 删除**所有**圆括号及内容（含正文正常括号，如"我昨天去了（公园）"→"我昨天去了"） |
| 34 | `core/mood.py:219-251` | 特殊日子心情加成被漂移覆盖：加成后 `db.set_mood` 刷新了库内时间戳，但局部变量 `updated` 仍是昨天 → hours≈24 → `_drift` 的 `pull=min(1,0.08*24)=1` **全额拉回基线**，生日加成确定性地被吃掉（已验证 `_drift` 公式 ✓） |
| 35 | `core/mood.py` + `persona.py:88` | `current_mood → today_weather` 是同步 urllib 请求（超时 8~20s），affection 路径有线程池预热掩盖，但 greeting/API 等直接调用路径在缓存 miss 时阻塞事件循环 |
| 36 | `core/userdb.py:726-759` | `reset()` 重建连接后未恢复 `journal_mode=WAL`/`busy_timeout`/`synchronous` 三项 PRAGMA，重置后并发写可能 `database is locked` |
| 37 | `core/affection.py:323-328` + `userdb.py:334-337` | `_daily_penalty_total` 统计当天全部负 delta，含"手动设置"写入的负值 → 手动调低好感度后，当天刷屏/辱骂扣分被每日上限错误阻止（低频，管理操作相关） |

P3（低危备忘）：`affection.py:59-63` 排除词表全是 ABUSE_WORDS 里不存在的词（死代码）；即时奖励 `try_daily_bonus` 不经过心情缩放而基础奖励经过（口径不一）；`userdb.update_task` 从 completed 改回 pending 不清 `completed_at`；`log.py` stderr handler 未设 encoding，Windows GBK 控制台中文日志可能乱码；`llm.extract_address` 不校验返回长度/内容。

---

## 六、修复优先级建议

1. **立即**：P0-1（收窄 CORS + 关键端点加 token + 确认钩子无通道时 deny）、P0-2（web_fetch 白名单）
2. **本周**：P1-1（pipeline 时序）、P1-14（跨天补总结）、P1-12/13（facts 游标 / chat_stream 重试）、P1-4（确认钩子异常 deny）、P1-5/6（记忆引擎线程问题）、P1-9（子进程 kill）、P1-8（黑名单补全 + open_app）
3. **随后**：P2-34（心情加成被漂移吃掉）、P1-2（流式）、P1-3（tool_call id）、P1-10/11 及其余 P2 清单

> 备注：core 次要模块的并行审查已完成，其新发现经主审交叉验证后合并进第五节。
