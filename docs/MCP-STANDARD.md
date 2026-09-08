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

## 接一个服务器（三步）

**1. 起服务器**（以 Playwright MCP 为例，本机 HTTP 模式）：

```bash
npx @playwright/mcp@latest --port 8931
# 具体 flag 以 `npx @playwright/mcp@latest --help` 为准：
# 不同版本可能是 --port / --transport http / --host 组合
```

**2. 允许回环地址**（本地服务器跑在 127.0.0.1，默认被 SSRF 防护拒绝）：

```ini
# .env
AGENT_MCP_ALLOW_LOOPBACK=1
```

> 这是**仅作用于 MCP 注册**的显式开关；`web_fetch` 等其它出网路径的 SSRF 防护不变。

**3. 注册**：设置页 → MCP 服务器 → 填名称 + URL（如 `http://127.0.0.1:8931/mcp`）。
注册成功后远程工具以 `服务器名::工具名` 出现在工具表里，类别 `external`、**默认需要确认**。

## 演示时的注意点

- MCP 工具注册为 `category=external, needs_confirm=True`。**若演示模式开着（`AGENT_DEMO_MODE=1`），它们会自动执行**——
  第三方公网服务器意味着数据会发出去。演示完请把演示模式改回 `0`。
- 工具调用结果进入对话前会经过截断（4000 字符）与总预算（32KB）控制；失败会给结构化错误。
- 卸载：设置页删除该服务器，或 `DELETE /api/mcp/servers/{name}`。

## 已验证 / 未验证

**已验证**（`tests/test_mcp_client.py`，4 项）：
- Streamable HTTP 的 JSON 与事件流两种响应；
- 旧版 HTTP+SSE 自动回退；
- 回环默认拒绝、`AGENT_MCP_ALLOW_LOOPBACK=1` 后放行；
- 注册 → 工具进全局注册表（`external` + 需确认）→ 调用 → 卸载 端到端。

**未验证**：真实第三方服务器（本机沙箱的 npm 代理不通，`npx` 取不到包）。
请在你自己的终端按上面三步跑一次，确认：注册成功、工具列表出现、调用有结果。

## 排查

- 注册失败看后端日志 `[MCP] 连接外部服务器失败: <名称>（<原因>）`：
  - `拒绝访问不安全的服务器地址` → 忘了开 `AGENT_MCP_ALLOW_LOOPBACK`，或地址写成了内网 IP；
  - `HTTP 404` → 端点路径不对（Streamable 常见 `/mcp`，SSE 常见 `/sse`）；
  - 握手超时 → 服务器没起、端口不对，或本机代理干扰（仓库 `.env` 的 `LLM_PROXY=off` 只作用于模型调用，npm/npx 走系统代理）。
- 旧版服务器文件 `data/mcp_servers.json` 里若残留过旧协议的条目，重启后会连接失败并保留登记，删掉重加即可。
