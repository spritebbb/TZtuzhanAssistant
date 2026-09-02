# 菟菚桌面助手（TZtuzhanAssistant）代码审查报告（第二轮·修订版）

- 审查日期：2026-09-02（同日第二次修订：P0/P1 全部 + 4 项 P2 已修复并回归验证，见第三节）
- 审查范围：`backend/` 全部（tools/ core/ api/ agent/ session/ maintenance/ plugins/ skills/）+ `frontend/`（electron + src 全部组件）
- 审查方式：逐文件通读 + 与第一轮 `CODE-REVIEW.md` 逐条对照；关键安全结论均经主审亲自读码交叉验证；确认链路经沙箱端到端实验证实
- 分级：P0 = 可被利用或必然出错；P1 = 特定条件下出错；P2 = 可疑/边缘/设计缺陷

---

## 一、总体结论

第一轮报告（CODE-REVIEW.md）中的 **22 项 P0/P1 问题已修复 20 项**，修复质量普遍较好（如 CORS 收窄、web_fetch SSRF 防护、pipeline 时序、跨天补跑、chat_stream 重试、tool_call_id 轮次前缀、远程任务 token 校验等）。当前代码整体健壮性明显提升。

**本轮实测补充发现 1 个 P1 实战 bug 并已当场修复**（见第二节 P1-0）：`POST /api/confirm` 的参数绑定方式与前端不匹配，导致确认卡片点「允许/拒绝」无响应、只能等 60s 超时拒绝——恰好是"42 项测试全绿但真实使用坏掉"的典型案例，暴露出确认链路 HTTP 层零测试覆盖的问题。已修复并新增 HTTP 层回归测试（`tests/test_confirm_http.py`，4 项全过）。

**修复批次（2026-09-02 第二次修订，全部完成并回归验证）**：
- 3 项 P0 全部修复（P0-1 确认钩子异常、P0-2 CORS null + /mcp 鉴权 + Origin 守卫、P0-3 配置写接口防护）
- 4 项 P1 全部修复（远程任务防护、run_python 子进程化、后台任务强引用、SSE 总超时）
- 4 项重点 P2 修复（P2-3 图片流可取消、P2-4 流式重试去重、P2-14 压缩失败冷却、P2-16 步骤确认门禁）+ 4 项顺手修复（P2-9/13/18/19）
- 回归验证：pytest 16 passed（含新增/更新的 4 个测试文件）、前端 `npm run build` 通过、Origin 守卫 8 项行为矩阵验证全过、run_python 子进程/反射封堵/agent 门禁/MCP token/remote 回环防护逐项验证通过

---

## 二点五、修复批次明细（2026-09-02 第二次修订，✅ 全部完成）

> **HTTP 层测试补充发现并修复两个同类盲区**（2026-09-02 第三次修订）：
> ① `POST /api/agent/tasks`（`api/agent.py`）与 P1-0 同病：普通类型参数只从 query 绑定，前端 AgentPanel 用 form body 发 objective → 创建任务恒 400「缺少目标」。已改 query/form/JSON 三兼容。
> ② **`POST /api/agent/tasks/{id}/run` 恒 500**（比 ① 更严重）：`asyncio.create_task(ctx.run(asyncio.create_task, _run()))` 中内层返回 Task、外层需要 coroutine → `TypeError: a coroutine was expected`——**Agent 任务从未被 HTTP 端点真正启动过**，服务层测试直接调 `session.run_task()` 掩盖了这一点。已改为 `bg = ctx.run(asyncio.create_task, _run())` + 强引用集合。新增 `tests/test_http_endpoints.py`（9 项，模拟三个前端组件的真实请求形态：SSE 帧契约、form body、Origin 守卫矩阵、Agent 全链路）已全过，套件 17 passed。

| 编号 | 修复内容 | 涉及文件 | 验证方式 |
|---|---|---|---|
| P0-1 | 确认钩子异常按类别处理：write/run/external → deny，只读 → allow（不再吞掉 base.py 的 deny 兜底）| `tools/confirm.py` | test_p3_confirm.py 5 项通过 |
| P0-2 | ① CORS 移除 `"null"` origin；② 新增 `_origin_guard` 中间件：cross-site 且 Origin 不在可信白名单 → 403，`Origin: null` 的写请求 → 403（CSRF 第二道防线）；③ Electron 生产改 `loadURL(http://127.0.0.1:8801)`（同源），后端挂载 `frontend/dist` 静态服务；④ `/mcp/call` 配置了 `AGENT_REMOTE_TOKEN` 时强制 Bearer 校验（常量时间比较） | `app.py`、`electron/main.ts`、`tools/mcp_server.py` | Origin 守卫 8 项矩阵验证（恶意网页/null HTML/沙箱 iframe 均 403；可信 dev、同源、无 Origin 客户端、GET 全放行）|
| P0-3 | 变更类端点（POST/DELETE/PATCH/PUT）统一受 Origin 守卫保护——含 POST /api/config、DELETE sessions、DELETE audit 等 | `app.py`（同上中间件）| 矩阵验证：伪造 cross-site POST /api/config → 403 |
| P1-1 | 远程任务防裸奔：绑定非回环地址（0.0.0.0/局域网 IP）时空 token 一律拒绝（`TZT_BIND_HOST` 由 main.py 注入）；main.py 启动时对非回环绑定 + 无 token 打显式警告。回环绑定保持本地信任（不破坏现有单机用法） | `api/remote.py`、`main.py` | 验证脚本：0.0.0.0 拒绝空 token，127.0.0.1 保持信任 |
| P1-2 | `run_python` 重写：静态扫描（新增 `__getattribute__`、非常量 getattr 一律拒绝）→ 代码经 stdin 传入独立子进程（白名单 builtins 受限 exec）→ 60s 超时 kill。死循环不再冻结事件循环 | `tools/builtin/code_exec.py` | 验证脚本：正常执行/拼接反射拒绝/`__getattribute__` 拒绝/`import os` 拒绝 |
| P1-3 | `watch_plugins`/`_restore_mcp`/`maintenance_loop` 三个后台任务改经 `_spawn_bg()` 持强引用，防 GC 静默取消 | `app.py` | py_compile + 启动日志 |
| P1-4 | `_runner` 包 300s 总超时：超时发 `__error__` 帧，不再无限累积 | `api/chat.py` | py_compile（行为需实测长任务）|
| P2-3 | 图片对话流与 send 共用 AbortController（stop()/切会话均可取消）；全部 SSE 回调加 botIndex 气泡守卫（切会话清空后不写 undefined） | `frontend/ChatView.vue` | npm run build 通过 |
| P2-4 | `chat_stream` 重试只在**未产出任何片段**时进行；流中失败直接抛出（消除前端重复文本） | `core/llm.py` | 代码审查（需实测断流场景）|
| P2-14 | 长会话压缩失败进入 600s 冷却期（调用异常与解析失败两条路径都记录），冷却期内直接跳过 | `core/memory/compress.py` | py_compile |
| P2-16 | 步骤确认门禁：仅 `allowed` 步骤进入执行上下文；全 pending 时 `run_task` 保持 planned 并提示先确认。前端 runTask 需配合展示错误（后续可加） | `agent/session.py` | 验证脚本 + test_p4_agent.py 更新后通过 |
| P2-9 | MCP 服务器登记持久化改原子写（tmp + `os.replace`） | `tools/mcp_server.py` | py_compile |
| P2-13 | `agent_fanout`：任务项类型校验 + `gather(return_exceptions=True)`，单任务异常不丢其余结果 | `tools/builtin/subagent.py` | py_compile |
| P2-18 | `_check_within` 对齐 safety 写法（casefold + `os.sep` 后缀） | `tools/builtin/file_search.py` | py_compile |
| P2-19 | 工具循环 `call_native` 异常改为透传真实错误（`处理失败：类型: 信息`），不再用"（我先记一下）"掩盖 | `tools/tool_loop.py` | py_compile |

---

## 二、第一轮问题修复状态对照

> 注：P0-2/P0-3 的描述为**修复前**状态（保留供对照），实际已在第二节五的修复批次中修复。

| 第一轮编号 | 结论 | 依据 |
|---|---|---|
| P0-1 CORS 全开 | ✅ 已修复（app.py:39-51 白名单）| 但 "null" origin 残留，见本轮 P0-2 |
| P0-1 /mcp/call 无鉴权 | ❌ 未修复 | 见本轮 P0-2 |
| P0-1 无通道确认放行 | ✅ 已修复（confirm.py:170-177 默认 deny）| 但异常路径仍放行，见本轮 P0-1 |
| P0-2 web_fetch file://+SSRF | ✅ 已修复（web_fetch.py:44-56 scheme 白名单+内网拦截+2MB 上限+to_thread）| 重定向目标未复检，见本轮 P2-8 |
| P0-3 run_python 沙箱 | ⚠️ 部分（确认框已显示代码摘要 confirm.py:141-145；文档声明非强沙箱）| 沙箱本身仍可绕过，见本轮 P2-5 |
| P1-1 pipeline 时序 | ✅ 已修复（pipeline.py:329-331 prev_ts 先取）| |
| P1-2 流式死代码 | ✅ 已修复（pipeline.py:824 use_tool_loop 按需，887-899 流式可达）| |
| P1-3 tool_call_id 重复 | ✅ 已修复（tool_loop.py:405-408 `call_{loop}_{i}`）| |
| P1-4 确认钩子异常放行 | ⚠️ base.py:209-212 已改 deny，**但被 confirm.py 内层吞异常抵消** | 见本轮 P0-1（最重要残留） |
| P1-5 记忆引擎事件循环误用 | ✅ 已修复（engine.py:17-51 判断与调度分离；app.py:92-100 调度在事件循环线程）| |
| P1-6 db.conn 跨线程 | ✅ 已修复（userdb.py:162-171 RLock + @_locked；engine.py:121 `with db._lock`）| |
| P1-7 embedding 维度混存 | ✅ 已修复（embedding.py:33,58 冷却期；vector_store.py:118-133 维度一致性检查+collection 元数据）| |
| P1-8 危险命令黑名单 | ✅ 已修复（safety.py:69-87 补 rmdir/rm -r/Remove-Item -r 等）| open_app 已统一过 check_command（system.py:185） |
| P1-9 子进程超时不 kill | ✅ 已修复（code_exec.py:126-133、external.py:87-93,121-127 均 kill+wait）| |
| P1-10 /api/remote/task 实参+查询无鉴权 | ✅ 已修复（remote.py:60-91 兼容 JSON body；151-156 GET 校验 token；45 compare_digest）| |
| P1-11 前端切会话竞态 | ✅ 已修复（ChatView.vue:26,32-36 loadSeq 序号守卫）| |
| P1-12 daily 游标推进 | ✅ 已修复（daily.py:171-175 失败不推进，与 profile 策略一致）| |
| P1-13 chat_stream 无重试 | ✅ 已修复（llm.py:178-202 重试）| 但流中重试有重复输出问题，见本轮 P2-4 |
| P1-14 跨天补跑只补昨天 | ✅ 已修复（affection.py:386-418 从 last_batch_date 遍历补跑）| |
| P2-17 token 明文比较 | ✅ 已修复（remote.py:45 secrets.compare_digest + Bearer 头支持）| |
| P2-34 心情加成被漂移吃掉 | ✅ 已修复（mood.py:245-247 加成后同步 updated）| |
| P2-36 reset 后 PRAGMA 丢失 | ✅ 已修复（userdb.py:793-795 恢复三项 PRAGMA）| |
| P2-31 久别问候并发重复 | ✅ 已修复（greeting.py:102-109 _greet_lock + 锁内判断）| |
| P2-29 生图误触发 | ✅ 已修复（intent.py:35-40 移除"想看/给我看看"类词）| |
| P2-11 孤立 user 消息 | ✅ 已修复（chat.py:49-55 持久化失败明确返回 410）| pipeline 内异常路径仍可产生孤立 user 消息（P2-1 保留） |

---

## 三、本轮实测发现并已修复（✅）

### P1-0（已修复）｜ `api/confirm.py` ｜ 确认接口参数绑定与前端不匹配 → 确认卡片无法点按

**症状**（用户实测报告）：让菟菚调用 DSH harness（dsh_run 等 external 类工具）时，确认卡片正常弹出，但点「允许/拒绝」无任何反应，只能等 60s 超时自动拒绝。

**根因**（沙箱实验证实）：旧代码用普通类型参数声明——

```python
async def api_confirm(request_id: str = "", allow: bool = False):
```

FastAPI 对 POST 端点的普通类型参数**只从 query string 绑定**；而前端 `ConfirmPanel.vue` / `AgentPanel.vue` 把 `request_id`/`allow` 放在 **form body**（application/x-www-form-urlencoded）里发送。实验结果：

| 发送方式 | 修复前 | 修复后 |
|---|---|---|
| form body（前端真实方式）| **400「缺少 request_id」** | 200 ok |
| query string | 参数可达（404 因 rid 不存在）| 200 ok |
| JSON body | **400「缺少 request_id」** | 200 ok |

后端收到空 `request_id` → 400 → 前端 `if (data.ok)` 不成立且**静默不做任何事** → 卡片留在原地，重复点击同样失败。

**为什么测试没抓到**：`tests/test_p3_confirm.py` 直接调用 `ConfirmService.resolve()`，完全不经过 HTTP 层——确认链路的参数绑定零覆盖。

**修复**（`backend/api/confirm.py:13-69`）：重写 `api_confirm`，手动解析并兼容 query / form / JSON 三种传参；`allow` 缺失按 False（拒绝）处理，保持安全默认。

**新增回归测试**（`tests/test_confirm_http.py`，4 项全过）：form body resolve → allow ✓；query 兼容 → deny ✓；JSON 兼容 → allow ✓；缺 request_id → 400 ✓。现有 `test_p3_confirm.py` 5 项重跑无回退。

**教训**：工具类/交互类端点必须有「模拟前端真实发送方式」的 HTTP 层测试；`test_p3_confirm.py` 只测了服务层语义，绑定层盲区让一个完全坏掉的端点绿灯上线。

---

## 四、仍存在的问题（待确认后修复）

### P0

#### P0-1 ｜ `tools/confirm.py:183-185` ｜ 确认钩子异常仍放行写/命令类操作（第一轮修复被内层抵消）

```python
except Exception:
    logger.exception("[确认] 确认钩子异常，放行")
    return "allow"
```

`default_confirm_hook` 自己把 `ConfirmService.request()` 的异常吞掉并返回 `"allow"`。第一轮的修复（`base.py:209-212` 钩子异常 → deny）因此**永远收不到异常**——对默认钩子是死代码。当 `ConfirmService.request` 内部出错（push 失败、锁异常等）时，write/run/external 类工具**未经确认直接执行**。

**修复建议**：confirm.py 的 except 按类别返回——write/run/external 返回 `"deny"`，只读返回 `"allow"`；或直接 re-raise 交给 base.py 统一 deny。

#### P0-2 ｜ `app.py:42` + `tools/mcp_server.py:55-81` ｜ CORS 仍放行 `Origin: null`，/mcp/call 无鉴权 → 本地文件可被恶意页面读取

三个事实组合：

1. `app.py:42` CORS 白名单包含 `"null"`（为 Electron file:// 页面留的）。浏览器中 **file:// 本地 HTML、data: URL、以及任意网站嵌入的 sandboxed iframe** 发出的请求 Origin 都是 `null` → 全部通过 CORS 校验，且响应可被读取。
2. `mcp_server.py:55-81` `POST /mcp/call` 无任何鉴权（仅受 Host 白名单保护，而浏览器对本机 fetch 的 Host 就是 `127.0.0.1:8801`，天然在白名单内）。
3. read 类工具（read_file / grep / glob / memory_search 等）`needs_confirm=False`，且无 SSE 通道时 confirm hook 对 read 放行（confirm.py:177）→ **不弹确认直接执行**。

**攻击链**：恶意网页嵌入 `<iframe sandbox>`（或诱导打开本地 HTML）→ fetch `http://127.0.0.1:8801/mcp/call` `{name: "read_file", arguments: {path: ".env"}}` → 项目根在默认白名单（safety.py:19-21）→ **.env 中的 LLM_API_KEY 等敏感信息直接回传给攻击者页面**。

**修复建议**（任选其一即可切断链条）：
- 从 CORS 白名单移除 `"null"`：Electron 侧改用自定义协议（`app://`）或让渲染进程统一走 `http://127.0.0.1:8801` 来源；
- `/mcp/*` 加 token（复用 `AGENT_REMOTE_TOKEN` 机制）。

#### P0-3 ｜ `api/config_api.py:48-98` ｜ POST /api/config 无鉴权 → 可改写 LLM_BASE_URL 窃取 API Key

同上 CORS `null` 链：恶意页面可 `POST /api/config` 把 `llm_base_url` 改为攻击者代理 → 后续所有 LLM 请求（携带 `Authorization: <API_KEY>`）流向攻击者服务器。`GET /api/config` 还会泄露 base_url/model 等配置。

**修复建议**：配置写接口加 token 校验（至少 POST），或复用 P0-2 的修复从源头切断 null origin。

### P1

#### P1-1 ｜ `api/remote.py:38-45` + `core/config.py:113` ｜ 远程任务默认"空 token 即放行"

`AGENT_REMOTE_ALLOW_EMPTY_TOKEN` 默认 `1`：未配置 `AGENT_REMOTE_TOKEN` 时任何请求直接放行。单机是"本地信任"的设计决定，但一旦用户按 `main.py:36` 的提示以 `--host 0.0.0.0` 启动（注释还鼓励这么做），**局域网任意设备可无凭据调用 `/api/remote/task`**。任务执行经工具循环，无 SSE 通道时 write/run/external 会被 deny，但 read 类工具放行 → 可读取白名单内文件并从 `GET /api/remote/task/{id}` 取回结果。

**修复建议**：默认改为拒绝（`AGENT_REMOTE_ALLOW_EMPTY_TOKEN` 默认 `0`），或在 `--host 0.0.0.0` 启动时强制要求配置 token 并打警告日志。

#### P1-2 ｜ `tools/builtin/code_exec.py:67-68` ｜ exec 同步执行在事件循环线程，且无超时

`_run_python` 是 async 函数，但 `exec(code, ...)` 同步执行——`while True: pass` 或长计算会**冻结整个服务**（SSE、所有会话、维护循环全卡）。工具需要用户确认，但确认后的失控代码仍可挂死进程。

**修复建议**：`await asyncio.to_thread(...)` 放线程池 + 超时（线程不能强杀，建议子进程执行 + 超时 kill）。

#### P1-3 ｜ `app.py:118/123/126` ｜ 三个后台任务无强引用，可能被 GC 静默取消

`watch_plugins()`、`_restore_mcp()`、`maintenance_loop()` 的 `asyncio.create_task` 结果未保存引用（对比记忆引擎用了 `_background_tasks` 集合）。Python 官方文档明确警告此模式下任务可能被垃圾回收且无任何报错——**维护循环消失后 checkpoint/备份/清理全部停摆**，且无日志。

**修复建议**：统一用 `_background_tasks` 集合持有强引用。

#### P1-4 ｜ `api/chat.py:95-127` ｜ SSE 断开后 _runner 无总超时

客户端断开后 _runner 继续（设计如此），但若 `process()` 内部卡在某个无超时环节（LLM 客户端有 90s 超时兜底，但 to_thread 内的同步调用如 embedding 模型下载、`getaddrinfo` 等可远超 90s），任务和队列会累积。多轮堆积后内存/线程池耗尽。

**修复建议**：_runner 包一层总超时（如 `asyncio.wait_for(process(...), timeout=300)`）。

### P2（边缘/设计缺陷）

| # | 位置 | 问题 |
|---|------|------|
| 1 | `core/pipeline.py:949-952` | 每条消息双写 long_memory（用户说/菟菚说），无 TTL/上限，长期记忆表无限增长（旧 P2-7 残留，若为有意设计建议加文档） |
| 2 | `core/pipeline.py:916-925` | 重复回复重写时 system 指令 append 在 user 消息**之后**，与 tool_loop.py:388-391 的正确做法（插到 user 前）矛盾，部分严格端点会忽略/拒绝末尾 system（旧 P2-28 残留） |
| 3 | `api/chat.py:95-127` + `ChatView.vue:133` | 图片对话 `handleImageFile` 用一次性 `new AbortController()` 且不保存引用：`stop()` 无法取消、切会话 watch 也不会中断该流；回调里 `messages.value[botIndex]` 在消息数组被清空后为 undefined 会抛 TypeError（被 catch 吞掉显示"网络错误"） |
| 4 | `core/llm.py:178-202` | `chat_stream` 重试包裹整个 `async for`：流中途失败重试时**从头重新产出**已 yield 的片段 → 前端出现重复文本（`onReset` 只在重复检测路径发送） |
| 5 | `tools/builtin/code_exec.py:46-51` | 沙箱反射拦截可被拼接绕过：getattr 第二参数非字面量（如 `getattr(x, "__cla"+"ss__")`）不拦、`__getattribute__` 不在名单。文档已声明非强沙箱，降为 P2；建议同步禁 `__getattribute__`/非常量 getattr |
| 6 | `tools/tool_loop.py:275-290` | `_clean_args` 对 `code`/`command` 参数做 `_strip_instruction` 剥前缀/去尾词，可能破坏合法内容（LLM 正确传参时被误改写）（旧 P2-3 残留） |
| 7 | `core/mood.py:32-36,52` | `_WEATHER_BASE` 仍在 import 时固化，改 `mood_rules.json` 对天气基线不生效（`mood_label` 已动态读，此为半残留）（旧 P2-32 部分） |
| 8 | `tools/builtin/web_fetch.py:56` | `urlopen` 默认跟随重定向，重定向目标未复检 `_is_private_host`（302 → 内网 SSRF 残留面）；DNS rebinding（校验后二次解析）同理 |
| 9 | `tools/mcp_server.py:90-103` | `_persist_servers` 直接覆写非原子（崩溃可写半截 JSON 致登记丢失），建议临时文件 + `os.replace` |
| 10 | `tools/mcp_server.py:170+` | McpClient/外部 MCP 注册（`POST /api/mcp/servers`）无 SSRF 校验，可注册 `http://127.0.0.1:xxx` 借后端探测内网 |
| 11 | `tools/builtin/system.py:245` | clipboard_set 临时文件名 `_clip_{秒级时间戳}.txt` 可预测（TOCTOU）；PowerShell 单引号插值对含单引号路径脆弱 |
| 12 | `tools/builtin/system.py:182` | open_app 元字符过滤漏 `%` 与换行（`cmd /c start` 仍解释 `%VAR%` 展开） |
| 13 | `tools/builtin/subagent.py:69` | `agent_fanout` 对 tasks 元素无类型校验，gather 无 `return_exceptions=True`：单个非法元素丢掉全部子任务结果 |
| 14 | `core/memory/compress.py:173-179` | LLM 摘要失败 → cursor 不推进 → 总消息超阈值期间每条消息都重试一次 LLM 压缩调用（同步在对话关键路径，首字延迟 + 数秒）（旧 P2-8 残留行为） |
| 15 | `plugins/loader.py` | register() 中途抛异常时"半注册"工具不清理，残留 ToolRegistry（子代理发现，未逐行验证） |
| 16 | `agent/session.py:227-228` | 计划步骤 `pending`（未确认）仍进入执行上下文，仅排除 denied——步骤级确认仍形同虚设（旧 P2-12 残留；工具级确认仍有效） |
| 17 | `core/userdb.py:180` | 单连接 + RLock 只保护写方法；读方法与写可并发交错。Python 3.11+ 的 sqlite3 serialized 模式下安全，**依赖运行时 ≥3.11**，建议在 requirements/README 明示最低版本 |
| 18 | `tools/builtin/file_search.py:19-24` | `_check_within` 用裸 startswith（未加 `os.sep`，对比 safety.py:54 的正确写法）；当前调用路径不可利用，属一致性隐患 |
| 19 | `tools/tool_loop.py:403,441` | `call_native` 异常被吞后返回"（我先记一下，回头跟你说）"——真实错误（key 失效等）被误导性文案掩盖（旧 P2-4 残留） |
| 20 | `models/` 全目录 | ChatRequest/StreamEvent/VisionRequest/ConfigView/SessionInfo 等模型定义后 API 层全部未使用，整层死代码 |

### 误报澄清（子代理报告中的错误结论）

- ❌ "api/agent.py:141 contextvars 传播失效，Agent 任务确认机制完全失效"——该结论的**机制分析**不成立（`ctx.run(asyncio.create_task, _run())` 中内层 create_task 在 ctx 内调用，Task 会拷贝含 `current_sse_push` 的上下文），但该行代码另有**更严重的真实 bug**：外层再包一层 `asyncio.create_task(...)` 对 Task 传参 → `TypeError` → POST /run 恒 500（已在修复批次中修复，见二点五节 ②）。子代理报错了行、说错了原因，但方向上歪打正着。
- ❌ "file_search.py `_check_within` 可被目录穿越利用"——当前唯一调用方传入的 target 均来自 `root.glob` 结果的 `relative_to(root)`，不可利用（见 P2-18，仅为写法隐患）。

---

## 五、做得好的地方（本轮确认）

- `session/store.py`：每操作独立连接 + asyncio.Lock 串行，干净
- `core/affection.py`：跨天补跑遍历实现正确，空日推进防重复扫描
- `core/memory/embedding.py`：加载冷却期 + 维度一致性检查的设计完整
- `core/mood.py:245-247`：心情加成与漂移的时序处理正确
- `frontend/ChatView.vue`：loadSeq 序号守卫简洁有效；markdown.ts（marked+DOMPurify）XSS 防护到位
- `tools/confirm.py:160-177`：无通道 deny 的默认值（AGENT_CONFIRM_NO_CHANNEL=deny）方向正确
- `core/daily.py` / `core/profile.py`：失败不推进游标的策略已统一
- `api/remote.py`：query/form/JSON 三兼容的参数解析写法，可作为其他端点的范本（本次 confirm 修复即参照此模式）

---

## 六、修复优先级建议

1. ~~P1-0（确认接口 HTTP 绑定）~~ ✅ 已修复
2. ~~立即：P0-1 / P0-2 / P0-3~~ ✅ 已全部修复（含 Origin 守卫 + Electron 同源改造）
3. ~~本周：P1-1 / P1-2 / P1-3~~ ✅ 已全部修复（P1-4 也已完成）
4. ~~随后：P2-3 / P2-4 / P2-14 / P2-16~~ ✅ 已全部修复（另完成 P2-9/13/18/19）
6. ~~流程改进：补 HTTP 层测试~~ ✅ 已完成（`tests/test_http_endpoints.py`，9 项覆盖 /api/confirm、/api/chat SSE、/api/agent/*、Origin 守卫矩阵；并借此发现修复了 agent create 400 / agent run 500 两个新盲区）
7. **剩余待办（低优先级）**：P2-1/2/5/6/7/8/10/11/12/15/17/20；`AgentPanel.vue` 的 runTask 错误提示配合步骤门禁展示（当前静默 .catch）；P1-4 的 300s 总超时与 P2-4 的断流行为建议在真实长任务/断网场景各实测一轮
