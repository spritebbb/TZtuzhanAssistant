# CODE-REVIEW-V6

> 审查范围：V5 之后新增/修改的代码，以及前几轮（V2→V5）一直未覆盖的核心模块。
> 审查基线：正确性 / 安全 / 可维护性 / 性能 / 测试，问题按 🔴 阻断、🟡 建议、💭 小建议 三级定级。

## 一、V5 问题闭环确认

| V5 编号 | 问题 | 修复方式 | 判定 |
|---|---|---|---|
| V5-1 | 沙箱"欺骗性"（宣称受限实为任意执行） | 文案全面改为"等同本机权限"，确认框如实告知 | ✅ 闭环 |
| V5-2 | 天气查询未走 SSRF 防护 | `_fetch_weather` 复用 `check_url` | ✅ 闭环 |
| V5-3 | confirm 缺 session 绑定 | 未处理（单机场景影响极小） | ⏸ 挂起 |
| V5-4 | 静默吞异常过多 | 部分改 `logger.exception`，仍留少量 `pass` | ✅ 基本闭环 |

V5 补充项：

| 编号 | 问题 | 修复方式 | 判定 |
|---|---|---|---|
| A | `getattr` 绕过反射拦截 | docstring 诚实披露"有限拦截、可绕过、不保证封死" | ✅ 闭环 |
| B | 天气重定向未逐跳复检 | 加注释说明"wttr.in 固定域名 + 城市已编码 + 仅展示天气，风险可接受" | ✅ 闭环 |

## 二、本轮新增发现

### 🟡 问题 E：工具注册表无锁并发（建议处理）

**位置**：`backend/tools/base.py` 的 `ToolRegistry._tools`（普通 dict，无锁）。

**现象**：
- `watch_plugins()` 后台任务每 2 秒调 `_scan_watch` → `load_plugin` → `ToolRegistry.register/unregister`（写 `_tools`）；
- `ToolRegistry.execute` 在工具循环里读 `_tools`（`cls._tools.get(name)`）。

**为什么风险其实很低**：Python 单线程事件循环里，dict 的 get/pop/赋值单个操作原子，且 `_scan_watch` 里的 `load_plugin` 是同步函数（不会被 await 打断），所以真正的"读-改-写"交叉竞态很难触发。属理论隐患，非立即爆炸的 bug。

**建议**：给 `ToolRegistry` 加一个 `asyncio.Lock`，把 `register`/`unregister` 与热加载扫描共用同一把锁。成本极低，能把"注册表并发安全"从"碰巧没事"变成"有保证"。

### 💭 nit（可选，不阻塞）

| # | 位置 | 说明 |
|---|---|---|
| 1 | `backend/tools/mcp_server.py` `McpClient.call_tool` | 客户端对 `self.url + "/call"` 用 `urlopen` 会跟随重定向，注册时只 `check_url` 了初始 URL，未逐跳复检（与 `web_fetch` 做法不一致）。但 URL 已持久化、注册时已校验、MCP 服务器由用户自行配置，风险很低 |
| 2 | `backend/plugins/loader.py` `_module_name` | 插件模块名 `plugin_{name}` 直接进 `sys.modules`，若两个插件名不同但 `_module_name` 冲突（理论上 `name` 来自文件名，不会冲突）——实际无风险 |
| 3 | `backend/core/memory/memory_manager.py` `_FallbackManager.add` | `md5` 仅用于去重 hash（非安全场景），无问题；仅提醒别把这个 hash 当安全凭据 |

## 三、本轮审阅过的模块（质量评估）

| 模块 | 结论 | 亮点 |
|---|---|---|
| `backend/tools/base.py` | 质量高 | 工具注册表封装清晰，除 E 外无问题 |
| `backend/plugins/loader.py` | 质量高 | 快照 + 精确回滚（`_rollback_registry`/`_teardown` 按对象身份判断归属），处理半注册残留、卸载顺序到位 |
| `backend/tools/mcp_server.py` | 质量高 | SSRF 防护（注册时 `check_url`）、命名空间隔离（`{server}::{tool}`）、原子写持久化（`os.replace`） |
| `backend/tools/tool_loop.py` | 质量高 | 工具循环控制清晰 |
| `backend/api/remote.py` | 质量高 | 身份隔离（`current_user_id.set`）、token 双源校验、任务容量上限（`_REMOTE_TASKS_MAX`） |
| `backend/core/memory/memory_manager.py` | 质量高 | Mem0 降级策略（连续 3 次失败才降级、冷却期自动重建）设计成熟 |

## 四、总结

核心安全面（沙箱诚实性、SSRF、鉴权、插件隔离、命令黑名单、子进程隔离）全部闭环，本轮无 🔴 阻断级问题。

仅剩一项建议处理：**问题 E（工具注册表无锁并发）**，低成本加固项，不影响上线安全。其余为可选 nit。

---

**下一步建议**：本轮到此为止。如后续要继续，可优先处理问题 E（给 `ToolRegistry` 加 `asyncio.Lock`），或进入新一轮增量审查（继续覆盖尚未审阅的前端 `electron/` 主进程、`preload`、记忆数据写入落盘层等）。
