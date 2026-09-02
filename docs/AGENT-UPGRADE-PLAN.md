# 菟菚 Agent 升级整体设计方案

> 版本：v1.0（评审稿）
> 目标：把菟菚从"拟人聊天的 Web 助手"升级为**具备完整本机操控能力 + 可双向调用外部 AI（Codex / DeepSeek Harness）的 Agent 助手**，同时优化底层逻辑、加强工具调用能力，并对所有本机操作实施**每步确认**授权。
> 评审人：用户（拍板人）

---

## 0. 一句话总结

在现有 `D:\DSH\TZtuzhanAssistant` 基础上，重构后端为「对话流水线 + 工具系统 + 确认授权 + 外部 Agent 桥」四层架构：对话层保留菟菚人格与记忆，工具层扩展为可安全操控本机的工具集，新增**每步确认**机制（前端弹窗 → 用户允许/拒绝 → 执行/中止），并新增 `codex_run` / `dsh_run` 两个外部 Agent 工具，同时保留菟菚被 DSH 反向调用的 MCP 通道。

---

## 1. 现状盘点（基于代码调研）

### 1.1 已有架构

```
TZtuzhanAssistant/
├── backend/                      # FastAPI 后端（实际运行代码）
│   ├── app.py                    # 应用工厂，注册路由/启动任务
│   ├── main.py                   # uvicorn 启动入口（127.0.0.1:8801）
│   ├── api/                      # HTTP 路由：chat / sessions / vision / images / meta / config / tts / mcp_servers
│   ├── core/                     # 对话核心（persona/pipeline/llm/memory/mood/search/...）
│   ├── core/memory/              # 记忆引擎 v2（Chroma + embedding + 长短期记忆 + 事实抽取）
│   ├── tools/                    # 工具系统
│   │   ├── base.py               # FunctionTool + ToolRegistry（全局注册表）
│   │   ├── tool_loop.py          # 原生 Function Calling 循环 + 文本协议回退（484 行）
│   │   ├── mcp_server.py         # MCP 服务器（/mcp/tools + /mcp/call）+ 简化 MCP 客户端
│   │   └── builtin/              # 10 个内置工具
│   ├── plugins/loader.py         # 插件系统（plugins/*.py → 自动注册工具 + 热加载）
│   ├── session/store.py          # SQLite 会话存储
│   └── maintenance/loop.py       # 后台维护（WAL checkpoint 等）
├── core/                         # ⚠️ 死代码（backend 与 tests 全部引用 backend.core，此处无人引用）
├── plugins/                      # 用户插件（currency.py / hello.py）
├── skills/                       # 技能 md（code / deep-search / parallel-analysis / summary）
├── frontend/                     # Vue3 + Vite + Electron（ChatView / SessionList / SettingsPanel / ToolBar）
├── data/                         # bot.db / sessions.db / memes.json / 日志
└── tests/                        # 测试（test_live_chat / test_memory_v2 / check_data_consistency ...）
```

### 1.2 已有工具清单（`backend/tools/builtin/`）

| 工具 | 能力 | 现状问题 |
|---|---|---|
| `web_search` / `web_fetch` | 联网搜索/抓取 | 可用 |
| `file_ops` / `file_search` / `file_edit` | 文件读写/搜索/编辑 | 无操作白名单，任何路径都可读写 |
| `todo` | 待办管理 | 可用 |
| `memory` | 记忆读写（todo_create 等） | 可用 |
| `skill` | 技能加载 | 可用 |
| `subagent` (`agent_run` / `agent_fanout`) | 子代理编排 | 只调 LLM，无工具/无文件/无本机能力 |
| `code_exec` (`run_python` / `run_command`) | Python 沙箱 / 系统命令 | ⚠️ 黑名单式拦截极弱；`run_command` 用 `create_subprocess_shell`，无确认、无输出截断风险、路径不受限；`run_python` 沙箱仅靠字符串黑名单，可被绕过 |

### 1.3 关键问题清单

1. **顶层 `core/` 是死代码**（backend/tests 全部用 `backend.core`）→ 可直接删除/归档，减少维护面。
2. **工具执行无确认机制**：`run_command` / `file_edit` 等高风险工具直接执行，无法满足"每步确认"要求。
3. **工具循环是同步串行**：`tool_loop.py` 在 `process()` 内同步跑完所有轮次才返回，无法在中间插入"等用户确认"。
4. **`run_python` 沙箱形同虚设**：字符串黑名单可被 `__import__` 技巧绕过，存在真实风险。
5. **无本机操控工具集**：没有进程管理、窗口/应用、截图、剪贴板、系统信息等。
6. **无外部 Agent 桥**：没有 Codex / DSH 调用能力。
7. **DSH 反向调用**：菟菚的 MCP 端点是自定义简化版（非标准 MCP streamable HTTP），DSH 的 mcp-client 可能无法直接对接；需要补标准通道。
8. **工具结果无截断**：长输出（命令输出/文件内容）可能撑爆上下文。
9. **前端无确认 UI**：需要新增"工具确认请求"面板。

---

## 2. 目标架构

```
┌────────────────────────── 菟菚 前端 (Vue/Electron) ──────────────────────────┐
│  ChatView（流式对话 + 工具卡片）  │  ConfirmPanel（🔔 确认请求：允许/拒绝/超时）│
└──────────────┬───────────────────────────────┬───────────────────────────────┘
               │ /api/chat (SSE)               │ /api/confirm (POST)
┌──────────────▼───────────────────────────────▼───────────────────────────────┐
│                            FastAPI 后端 (backend/)                            │
│                                                                              │
│  ┌─────────────────────┐   ┌──────────────────────┐   ┌───────────────────┐  │
│  │ 对话流水线 pipeline  │──▶│ 工具循环 ToolLoop     │──▶│ 工具执行 ToolRegistry│  │
│  │ persona/记忆/好感度  │   │ 原生FC+文本回退        │   │ 内置工具+插件+外部  │  │
│  └─────────────────────┘   └──────────────────────┘   └───────────────────┘  │
│        ▲                            │  ▲                      │              │
│        │                            ▼  │                      ▼              │
│  ┌─────────────────────┐   ┌──────────────────────┐   ┌───────────────────┐  │
│  │ 确认授权服务 ConfirmSvc │◀─┤ 挂起/恢复 事件驱动      │   │ 本机操控工具集      │  │
│  │ 每步请求→用户批准→放行 │   │ (asyncio Event)       │   │ 命令/文件/进程/     │  │
│  └─────────────────────┘   └──────────────────────┘   │ 窗口/截图/剪贴板    │  │
│        ▲                                               └───────────────────┘  │
│        │                                                ┌───────────────────┐  │
│  ┌─────┴──────────────┐                        ┌───────▶│ 外部 Agent 桥      │  │
│  │ MCP 标准通道（被DSH调）│◀──────────────────────┼───────▶│ codex_run / dsh_run│  │
│  └────────────────────┘                        └───────▶│ 双向打通           │  │
└─────────────────────────────────────────────────────────┴───────────────────┘
```

### 分层职责

| 层 | 职责 | 关键改动 |
|---|---|---|
| **对话流水线** | 人格/记忆/好感度/意图，组装 prompt | 保持，仅解耦工具调用 |
| **工具循环** | 原生 Function Calling + 回退；**支持挂起等待确认** | 重构为可暂停/恢复 |
| **工具系统** | 注册表 + 统一执行 + 参数清洗 + 结果截断 + 审计 | 增强 |
| **确认授权** | 危险操作前置确认；请求队列 + 超时 + 拒绝处理 | 新增 |
| **本机操控工具** | 命令/文件/进程/窗口/截图/剪贴板/系统信息 | 新增 |
| **外部 Agent 桥** | codex_run / dsh_run + DSH 反向通道 | 新增 |

---

## 3. 分项设计

### A. 底层逻辑优化

**A1. 清理死代码**
- 删除顶层 `core/`（验证：`grep -r "import core" backend/ tests/` 全部为 `backend.core` 引用后删除；如有遗漏先迁移再删）。
- 删除 `backend/data/` 下的陈旧 db 文件（`bot.db` 等为历史遗留），统一数据目录为 `data/`。

**A2. 解耦 `pipeline.process()`**
- 现状：`process()`（backend/core/pipeline.py，46KB）内嵌工具调用逻辑。
- 改法：抽出一个 `ConversationService`，职责：组装消息 → 调 `ToolLoop.run()` → 拿最终文本 → 存档。`pipeline` 只保留人格/记忆/好感度注入。工具逻辑全部归 `tools/`。

**A3. 工具循环可暂停/恢复（核心改造）**
- 现状：同步串行，无法中途等用户。
- 改法：`run_tool_loop` 改为 **逐步（step）驱动**：
  - 每轮 LLM 返回 → 若含工具调用 → 对每个调用先过 `ConfirmSvc`（若需确认则**挂起**，向 SSE 推确认请求）→ 用户允许后执行 → 结果注入 → 下一轮。
  - 实现：`asyncio.Event` + `asyncio.wait_for(timeout)`，确认请求带 `request_id`，前端 POST `/api/confirm` 后 `set()`。
  - 超时（默认 60s，可配置）：自动按**拒绝**处理，并把"被拒绝"作为工具结果返回给 LLM，让它调整策略。
  - SSE 挂起期间：推送事件 `{"type":"confirm_request", ...}`，前端弹确认框；确认后继续推送 `piece`。

**A4. 记忆/上下文层**
- 记忆引擎 v2 已可用（Chroma + embedding + 长短期记忆），保持。
- 增加**工具审计日志**：每次工具调用记录到 `data/tool_log.jsonl`（时间、工具、参数摘要、结果摘要、确认状态、耗时），便于追溯与调试。

**A5. 配置统一**
- `backend/core/config.py` 增加开关分组：`[agent]`（是否启用确认、确认超时、危险命令黑名单路径、允许操作目录白名单、codex/dsh 路径）。

---

### B. 工具调用能力增强

**B1. 工具注册协议升级（`FunctionTool`）**
新增字段：
- `category`: `read | write | run | external`（只读/写/命令/外部）
- `needs_confirm`: `True/False`（是否必须确认；`run`/`write` 默认 True，`read` 默认 False）
- `danger_level`: `info | normal | high | critical`（用于确认框颜色与文案）
- `max_output_chars`: 结果截断上限（默认 4000，`read`/`run` 可调大）

**B2. 统一执行管线（`ToolRegistry.execute` 增强）**
```
execute(name, args, ctx)
  ├─ 校验：工具存在 → 参数清洗（复用现有 _clean_args）
  ├─ 授权：danger_level ≥ threshold → ConfirmSvc.request(...) 挂起等批准
  ├─ 执行：async/sync（sync 走线程池 + contextvars 拷贝，已有）
  ├─ 结果后处理：截断到 max_output_chars；失败转友好错误
  ├─ 审计：写 tool_log.jsonl
  └─ 返回 ToolResult(ok, output, confirmed, elapsed_ms)
```

**B3. 结果压缩**
- `compress_text()`：超长输出截断 + 保留头尾 + 中间摘要提示（避免撑爆上下文）。

**B4. 工具发现与提示**
- `/api/meta` 已暴露工具开关；扩展为返回完整工具清单（名称/类别/是否需确认），前端可显示"已就绪工具"。
- 工具循环注入提示按类别分组，避免把所有工具描述一次塞爆。

**B5. 原生 Function Calling 优先策略**
- 保持现有 `call_native` 优先、文本协议回退。增强：native 失败时自动降级文本协议（已有），再失败降级为普通回复（已有）。

---

### C. 本机操控（完整 Agent 能力 + 每步确认）

**C1. 新增本机工具集（`backend/tools/builtin/` 扩展）**

| 工具 | 类别 | 需确认 | 说明 |
|---|---|---|---|
| `run_command`（升级） | run | ✅ 必须 | 改用 `create_subprocess` 列表参数（避免 shell 注入）；危险命令黑名单升级为**路径/命令语义级**；输出截断；`cwd` 受白名单限制 |
| `run_python`（升级） | run | ✅ 必须 | 沙箱加固：禁用 `__builtins__` 危险入口、`ast` 静态扫描禁止 `import os/subprocess/...` + 禁止属性访问黑名单；执行放子进程 + 超时 |
| `system_info` | read | ❌ | CPU/内存/磁盘/系统/开机时间 |
| `list_process` / `kill_process` | read/run | kill ✅ | 进程列表 / 结束进程 |
| `list_window` / `activate_window` | read/run | activate ✅ | 窗口列表 / 激活指定窗口 |
| `open_app` | run | ✅ | 启动应用（路径/命令，如 `notepad`, `calc`, 已知路径） |
| `screenshot` | read | ❌ | 截取屏幕保存到 `data/screenshots/`，返回图片路径（前端可显示） |
| `clipboard_get` / `clipboard_set` | read/write | set ✅ | 剪贴板读写 |
| `file_ops` 升级 | write | 写/删/改名 ✅ | 操作路径限制在**白名单根目录**内（默认 `D:\DSH\TZtuzhanAssistant\workspace` + 用户显式允许的目录），禁止系统目录 |
| `file_edit` 升级 | write | ✅ | 同上 |
| `browser_open` | run | ✅ | 打开 URL（默认浏览器） |

**C2. 安全白名单（`config.py` + `data/allowed_paths.json`）**
- `allowed_roots`: 允许读写的根目录列表（默认 `workspace/`，用户可在设置面板添加）。
- 所有文件/命令工具在执行前校验目标路径是否落在白名单内，越界 → 拒绝 + 审计。
- 危险命令黑名单（语义级）：`format`、`rd /s`、`rm -rf`、`del /f`、`shutdown`、`reg delete`、`diskpart`、`net user`、`takeown`、`icacls`、`taskkill /f /im`（系统关键进程）、`powershell -enc` 等 → 直接拒绝（不弹确认，白名单冲突时连确认都不给）。

**C3. 完整 Agent 执行模式（长任务）**
- 新增 `AgentSession`（`backend/agent/`）：
  - 用户下发任务 → LLM 生成**任务计划**（步骤列表）→ 前端展示计划 → 用户可逐条允许/拒绝/整体运行。
  - 逐步执行：每步 = 工具调用；执行前经确认；步骤结果回填计划；LLM 依据结果决定下一步或完成。
  - 支持"允许本次 + 记住本次会话"（会话级放行，减少重复确认）与"本次拒绝"。
  - 状态：`planned → awaiting_confirm → running → done/failed/cancelled`，持久化到 SQLite（`data/agent_sessions.db`）。
- 与现有对话的关系：长任务通过特殊命令触发（如「帮我做任务：<目标>」或前端按钮）；普通对话仍走原有工具循环。

**C4. 确认请求时序（前端交互）**

```
用户: 帮我打开记事本，写一首诗保存到桌面
  │
  ▼
pipeline → LLM 计划 → SSE: {type:"plan", steps:[...]}
  ▼
前端 ConfirmPanel 显示计划 → 用户点「允许运行」
  ▼
步骤1: open_app(notepad) → ConfirmSvc → SSE:{type:"confirm_request",
        request_id, tool, args, danger:"normal", message:"启动记事本应用?"}
  ▼
用户点「允许」→ POST /api/confirm {request_id, allow:true}
  │
  ▼
asyncio.Event.set() → 执行工具 → 结果注入
  ▼
步骤2: file_write(桌面/诗.txt) → 同上确认 → 执行
  ▼
LLM 收尾 → SSE: {type:"done", content:"写好了..."}
```

---

### D. 外部 Agent 双向打通（Codex / DeepSeek Harness）

**D1. 菟菚 → Codex（`codex_run` 工具）**
- 定位：本机已装 Codex CLI（`%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe`），且已配置 DeepSeek profile（`~/.codex/deepseek.config.toml`，`codex -p deepseek`，key 走 `DEEPSEEK_API_KEY`）。
- 工具定义：
  ```
  codex_run(prompt, cwd=workspace, model="deepseek-v4-pro", timeout=180)
  ```
- 实现：`create_subprocess` 调用 `codex.exe exec -p deepseek --skip-git-repo-check -C <cwd> --ephemeral --color never --output-last-message <tmpfile> "<prompt>"`（非交互，不依赖 TTY），最终回复从 `--output-last-message` 文件读取，stdout 兜底解析；子进程注入 `CODEX_HOME` 与 `DEEPSEEK_API_KEY`，超时/失败转友好错误；结果截断注入。
- 需确认：`category=external`，`needs_confirm=True`（外部调用消耗 token / 执行命令，必须先确认）。
- 环境：执行前确保 `DEEPSEEK_API_KEY` 注入子进程 env（从项目 `.env` 读取，不落日志）。

**D2. 菟菚 → DeepSeek Harness（`dsh_run` 工具）**
- 定位：DSH 已安装（checkout `D:\DSH\deepseek-harness`，CLI `apps/cli`），支持 headless 一次性任务：
  `dsh --profile headless "任务"`（answer one task, print result, and exit）。
- 工具定义：
  ```
  dsh_run(prompt, profile="headless", timeout=600)
  ```
- 实现：调用 `node D:\DSH\deepseek-harness\apps\cli\lib\bin.js --profile headless <prompt>`（或系统 `dsh` 命令，若在 PATH），捕获输出；超时/失败处理。
- 需确认：`category=external`，`needs_confirm=True`。
- 用途：把复杂任务（多步骤、需要 DSH 全部工具链）外包给 DSH 执行，菟菚拿回结果组织回复。

**D3. 外部 → 菟菚（DSH 反向调用菟菚）**
- 现状：菟菚已有自定义 MCP 端点（`/mcp/tools` + `/mcp/call`），是简化 JSON-RPC。
- 改法：升级为标准 **MCP Streamable HTTP**（服务端）：
  - 支持 `initialize`、`tools/list`、`tools/call`（带 session 管理、JSON-RPC 2.0）。
  - DSH 侧通过其 `mcp-client`（packages/mcp/mcp-client）配置一个外部 MCP 服务器指向 `http://127.0.0.1:8801/mcp`，即可让 DSH agent 直接调用菟菚的工具。
- 同时保留 `/api/remote/task`（简易 HTTP JSON 接口）作为低门槛通道：POST `{prompt}` → 返回菟菚的最终回复（走同一 pipeline）。
- 鉴权：默认仅本机（127.0.0.1）；暴露局域网时需 token（`data/.remote_token`）。

---

## 4. 安全与授权机制（汇总）

| 层级 | 策略 |
|---|---|
| 只读工具（搜索/抓取/读文件/截图/系统信息/进程列表） | 自动执行，不确认 |
| 写/命令工具（写文件/删文件/执行命令/打开应用/剪贴板写/结束进程） | **每步确认**（前端弹窗，允许/拒绝/超时自动拒绝） |
| 危险命令（格式化/删系统文件/关机/改用户等） | **直接拒绝**，不弹确认，审计记录 |
| 文件路径 | 白名单根目录内才可操作，越界拒绝 |
| 外部 Agent（codex_run/dsh_run） | 每步确认（消耗 token / 可能动文件） |
| 确认超时 | 默认 60s，超时按拒绝处理 |
| 会话级放行 | 用户可勾选「本次会话记住我的允许」，避免高频重复确认 |
| 审计 | 所有工具调用落盘 `data/tool_log.jsonl`，含确认状态与结果摘要 |

---

## 5. 实施计划（分阶段，每阶段可验证）

| 阶段 | 内容 | 验证方式 |
|---|---|---|
| **P0 基线** | 备份现状；删除顶层 `core/` 死代码；清理遗留 db；确保现有服务能启动 | 启动后端 + 一次测试对话跑通 |
| **P1 底层优化** | 抽 `ConversationService`；工具循环改逐步驱动（可暂停/恢复）；配置分组 | 现有测试通过 + 工具循环单测（mock 确认） |
| **P2 工具增强** | FunctionTool 协议升级（category/needs_confirm/截断）；统一执行管线；结果压缩；审计日志 | 工具单测 + `/api/meta` 返回完整清单 |
| **P3 确认机制** | ConfirmSvc + SSE confirm_request + `/api/confirm` + 前端 ConfirmPanel | 实测：触发写/命令工具 → 前端弹窗 → 允许/拒绝/超时三种路径 |
| **P4 本机操控** | 新增系统/进程/窗口/截图/剪贴板工具；白名单与危险命令黑名单；AgentSession 长任务模式 | 实测「打开记事本写文件」「截图」等链路，每步确认 |
| **P5 外部桥** | codex_run / dsh_run；升级 MCP 为标准 Streamable HTTP；/api/remote/task | 实测调用 codex 完成任务 + 从 DSH 侧通过 MCP 调菟菚 |
| **P6 回归** | 全量测试 + 浏览器实测多轮对话/工具/确认/长任务/外部调用 | 交付演示 + 测试报告 |

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 工具循环改造破坏现有对话 | P1 单独阶段，保留旧路径开关（`TOOL_LOOP_V2`），逐步切换 + 回归测试 |
| `run_command` 误执行危险操作 | 语义级黑名单 + 每步确认 + 白名单 cwd + 审计；拒绝不弹确认的危险命令 |
| 确认挂起导致 SSE 卡死 | 超时自动拒绝 + 前端倒计时 + 请求可取消 |
| Codex/DSH 调用慢/无响应 | 超时兜底 + 后台 job 模式（不阻塞对话，完成后再推送） |
| MCP 标准兼容工作量 | 优先实现最小标准子集（initialize/tools/list/tools/call），DSH 侧可连即达标 |
| Python 沙箱被绕过 | 静态扫描 + 限制 builtins + 子进程隔离 + 超时；不追求绝对安全（本地信任场景） |
| 前端改动量大 | ConfirmPanel 独立组件，最小侵入 ChatView；先做后端接口 mock |

---

## 7. 验收标准

1. **底层逻辑**：顶层 `core/` 已清理；工具循环支持逐步驱动与确认挂起/恢复；无回归（对话/记忆/好感度行为不变）。
2. **工具调用**：≥ 15 个内置工具（含本机操控）；统一协议（类别/需确认/截断）；审计日志落盘；结果超长自动压缩。
3. **本机操控**：能指挥菟菚完成一条完整真实链路（如「打开记事本写个文件再截图给我看」），且**每一步都弹确认**；拒绝/超时路径工作正常；危险命令被直接拒绝。
4. **外部打通（双向）**：
   - 菟菚 → Codex：`codex_run` 能完成一个真实任务（如「写一个 Python 脚本」）并返回结果；
   - 菟菚 → DSH：`dsh_run --profile headless` 能完成一个真实任务并返回结果；
   - DSH → 菟菚：DSH 侧配置 MCP 指向菟菚 `/mcp`，能 list/call 菟菚的工具。
5. **安全**：所有写/命令/外部操作均有确认或拒绝；`tool_log.jsonl` 有完整审计。
6. **前端**：确认弹窗、计划面板、工具状态展示可用。

---

## 8. 明确不做（范围边界）

- 不做远端/公网部署（仍限本机 + 可选局域网）。
- 不做语音实时交互的扩展（TTS 已有，本次不动）。
- 不做 LLM 换模型（沿用现有 OpenAI 兼容端点）。
- 不追求 Python 沙箱的绝对安全隔离（本地可信场景，重点是防误操作而非防恶意代码）。
- 本项目为独立仓库，不依赖/不改动任何外部 QQ bot 代码（原 `D:\DSH\TZtuzhan` 目录已不存在，仅存于历史文档记录中）。
