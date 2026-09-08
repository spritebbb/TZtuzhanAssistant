# MCP 标准协议接入（2026-09-09 升级）

> 背景：用户要求把 MCP 通道升级到标准协议，以便接生态里的真实 MCP 服务器（演示 Agent 功能）。
> 执行：ZCode。旧实现是自研的 `GET /tools` + `POST /call` 简化桥，接不上任何标准服务器。

## 升级后支持什么

| 传输 | 说明 | 自动探测 |
|---|---|---|
| **Streamable HTTP**（2025-03-26 / 2025-06-18） | 单端点 POST JSON-RPC；响应可为 `application/json` 或 `text/event-stream` | 先试这个 |
| **HTTP+SSE**（2024-11-05） | GET 事件流拿 `endpoint`，再向该 endpoint POST | 上面失败后回退 |

握手/调用全程走标准方法名：`initialize` → `notifications/initialized` → `tools/list` → `tools/call`；
会话头 `Mcp-Session-Id` 自动回带，`MCP-Protocol-Version` 按协商版本发送。

## 接一个服务器（推荐路径：stdio 桥，已为你配好）

生态里大多数 MCP 服务器（含 **Playwright MCP 的默认模式**）只讲 **stdio**，
而菟菚讲 Streamable HTTP。所以配了一个 **stdio→HTTP 桥**，不用去猜服务器的
HTTP 参数：

```bat
:: 双击运行（或命令行）
scripts\start-mcp-playwright.bat
```

它等价于：

```bash
.venv/Scripts/python.exe -m backend.tools.mcp_stdio_bridge --port 8932 -- npx --yes @playwright/mcp@latest
```

然后：

1. `.env` 里 `AGENT_MCP_ALLOW_LOOPBACK=1`（**已配**）；
2. `data/mcp_servers.json` 已预登记 `playwright → http://127.0.0.1:8932/mcp`（**已配**）；
3. **先跑桥脚本，再启动菟菚**（`start.bat`）——后端启动时自动连接并注册工具；
   若顺序反了，在设置页重新点一次「添加」或重启后端即可。

工具会以 `playwright::browser_navigate` 这类名字出现。

### 换成别的 stdio 服务器

```bash
python -m backend.tools.mcp_stdio_bridge --port 8932 -- npx --yes @modelcontextprotocol/server-github
```

再把 `data/mcp_servers.json` 里的 url 保持不变（桥的地址不变），名称改成新的即可。

## 直接用服务器自带的 HTTP 模式（可选）

若服务器自己支持 HTTP（如 `--port` 之类参数），也可以跳过桥，直接注册它的地址：

| 传输 | 常见端点 |
|---|---|
| Streamable HTTP | `http://127.0.0.1:PORT/mcp` |
| 旧版 HTTP+SSE | `http://127.0.0.1:PORT/sse` |

菟菚会自动探测是哪一种。

## 演示时的注意点

- MCP 工具注册为 `category=external, needs_confirm=True`。**若演示模式开着（`AGENT_DEMO_MODE=1`），它们会自动执行**——
  第三方公网服务器意味着数据会发出去。演示完请把演示模式改回 `0`。
- 工具调用结果进入对话前会经过截断（4000 字符）与总预算（32KB）控制；失败会给结构化错误。
- 卸载：设置页删除该服务器，或 `DELETE /api/mcp/servers/{name}`。

## 已验证 / 未验证

**已验证**（`tests/test_mcp_client.py` 4 项 + `tests/test_mcp_stdio_bridge.py` 2 项）：
- Streamable HTTP 的 JSON 与事件流两种响应；
- 旧版 HTTP+SSE 自动回退；
- 回环默认拒绝、`AGENT_MCP_ALLOW_LOOPBACK=1` 后放行；
- 注册 → 工具进全局注册表（`external` + 需确认）→ 调用 → 卸载 端到端；
- stdio 桥：HTTP → 子进程 stdin/stdout 转发、通知 202、id 解耦、子进程退出返回结构化错误。

**未验证**：真实 Playwright MCP（本次设置环境无外网，`npx` 取不到包）。
请在你自己终端双击 `scripts\start-mcp-playwright.bat`，看到 `🌉 MCP stdio 桥已启动`
后启动菟菚，确认设置页里出现 `playwright::*` 工具并能调用。

## 排查

- 注册失败看后端日志 `[MCP] 连接外部服务器失败: <名称>（<原因>）`：
  - `拒绝访问不安全的服务器地址` → 忘了开 `AGENT_MCP_ALLOW_LOOPBACK`，或地址写成了内网 IP；
  - `HTTP 404` → 端点路径不对（Streamable 常见 `/mcp`，SSE 常见 `/sse`）；
  - 握手超时 → 服务器没起、端口不对，或本机代理干扰（仓库 `.env` 的 `LLM_PROXY=off` 只作用于模型调用，npm/npx 走系统代理）。
- 旧版服务器文件 `data/mcp_servers.json` 里若残留过旧协议的条目，重启后会连接失败并保留登记，删掉重加即可。
