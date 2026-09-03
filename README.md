# 菟菚桌面助手（TZtuzhanAssistant）

> 类 DeepSeek Harness 的拟人 AI 助手 — **Electron 本地桌面版本**。
> 完全脱离 QQ / NoneBot，单用户 · 本机运行 · 完整人格 · 记忆 · 好感度 · 插件化工具链。

## 特性

- 🎭 **拟人人格**：毒舌腹黑、地狱笑话，人格写在 `persona-菟菚.md`
- 🧠 **长期记忆**：SQLite 存储用户画像、关系事实（五元组）、话题延续、重要日子；本地 BGE 向量语义检索
- 💖 **好感度系统**：昵称、关心、分享、道歉、夸奖等互动实时增减，语义感知 + 关键词兜底双通道
- ⛅ **心情系统**：绑定城市天气，心情随当日天气波动
- 🔎 **联网搜索**：博查 API 优先，bing/ddg 兜底；TTL 缓存
- 🎨 **文生图**：SiliconFlow Qwen-Image，SSE 回传图片
- 👁️ **识图**：拖拽或粘贴图片，视觉模型描述内容
- 🧰 **插件化工具链**：工具全部以 `plugins/*.py` 插件形式加载，支持热加载（无需重启）、每步确认机制、外部 MCP 服务器接入
- 🤖 **外部 Agent 桥**：`codex_run` / `dsh_run` 可派发独立任务给本机 Codex CLI / DSH（非交互 exec 模式，每步确认）
- 💬 **流式输出**：SSE 打字机效果 + 工具执行进度实时推送，Markdown 渲染，可随时停止
- 📂 **多会话 + 归档**：SQLite 存储，共享同一人格记忆，支持归档与关键词搜索
- 🕐 **主动性引擎**：久未聊且关系够近时主动开口，支持桌面通知
- 👋 **久别问候**：长时间未见再次打开时主动问候
- 🖼️ **静态立绘**：桌面窗口显示菟菚人设图

## 快速开始

```powershell
# 1. 后端：创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 2. 配置
copy .env.example .env
# 编辑 .env，至少填 LLM_API_KEY（详见下方「配置项」）

# 3. 启动后端（独立模式）
python -m backend.main

# 或者 4. 启动桌面应用（前后端一起）
cd frontend
npm install
npm run dev          # 开发模式（Vite + Electron 热重载）
npm run dist:win     # 打包 Windows 安装包
```

> **打包版注意**：`dist:win` 产出的安装包**不内置 Python 运行时与 LLM 依赖**，
> 安装后 Electron 会检测本机 8801 端口是否有后端；没有的话需要自行先启动后端
> （`python -m backend.main`）再打开应用。本机开发/日常使用推荐 `npm run dev`。

## 项目结构

```
backend/                      # Python 后端
├── main.py                   # 启动入口（--host/--port，默认 127.0.0.1:8801）
├── app.py                    # FastAPI 应用工厂（路由挂载 + 中间件 + 工具注册）
├── api/                      # API 路由层
│   ├── chat.py               # SSE 流式对话
│   ├── sessions.py           # 会话 CRUD + 归档 + 归档搜索
│   ├── vision.py             # 识图
│   ├── images.py             # 图片 / 立绘 / 截图服务
│   ├── meta.py               # 工具状态 + 心情
│   ├── config_api.py         # 配置编辑
│   ├── greeting.py           # 久别问候
│   ├── initiative.py         # 主动消息投递 + SSE 长连接
│   ├── agent.py              # 外部 Agent 任务（Codex/DSH 派发 + 逐步确认）
│   ├── remote.py             # 受控远程任务端点
│   ├── confirm.py            # 每步确认机制
│   ├── audit.py              # 操作日志
│   ├── mcp_servers.py        # 外部 MCP 服务器管理
│   ├── plugins.py            # 插件管理（启停/热加载/源码查看）
│   ├── tts.py                # 语音朗读
│   └── health.py             # 健康检查 + 优雅关停
├── core/                     # 对话核心
│   ├── pipeline.py           # 对话主流程
│   ├── llm.py                # LLM 客户端（主模型 + 感知层小模型）
│   ├── perception.py         # 语义感知（情绪/辱骂/关心等分类）
│   ├── persona.py            # 人格注入
│   ├── affection.py          # 好感度
│   ├── mood.py               # 心情
│   ├── memory/               # 记忆系统（事实/话题/五元组/日期记忆 + 压缩）
│   ├── userdb.py             # SQLite 数据层
│   ├── search.py             # 联网搜索
│   ├── imagegen.py           # 文生图
│   ├── vision.py             # 识图
│   ├── initiative.py         # 主动性引擎
│   ├── greeting.py           # 久别问候逻辑
│   └── ...                   # 更多模块
├── tools/                    # 工具层
│   ├── base.py               # 工具注册表
│   ├── tool_loop.py          # 工具调用循环（原生 Function Calling + 文本回退）
│   ├── service.py            # 工具轮次调度
│   ├── confirm.py            # 每步确认钩子
│   ├── mcp_server.py         # 内置 MCP 协议服务器（/mcp/*）
│   └── builtin/              # 内置工具（仅记忆系统；其余已插件化）
├── plugins/                  # 插件（web_search/web_fetch/file_ops/code_exec 等，热加载）
│   ├── loader.py             # 插件发现/加载/卸载/热加载
│   └── context.py            # 插件钩子上下文
├── agent/                    # 外部 Agent 桥
│   └── session.py            # Codex/DSH 任务会话
├── session/                  # 会话存储
│   └── store.py              # SQLite 会话/消息/归档持久化
├── skills/                   # 技能目录
├── maintenance/              # 后台维护（周期任务/截图清理/备份）
├── data/                     # 运行时数据（bot.db/sessions.db/截图等）
└── models/                   # 数据模型

frontend/                     # Electron + Vue 3 + TS 前端
├── package.json
├── vite.config.ts
├── electron/
│   ├── main.ts               # Electron 主进程（通知/后端拉起/进程管理）
│   └── preload.ts            # 预加载脚本
├── public/                   # 静态资源（sw.js 离线缓存/图标/立绘）
└── src/
    ├── App.vue               # 根组件
    ├── components/           # 组件
    │   ├── ChatView.vue      # 对话主界面
    │   ├── MessageBubble.vue # 消息气泡
    │   ├── ChatInput.vue     # 输入框
    │   ├── SessionList.vue   # 会话侧栏 + 归档搜索
    │   ├── Portrait.vue      # 立绘显示
    │   ├── ToolBar.vue       # 工具状态条
    │   ├── SettingsPanel.vue # 设置面板
    │   ├── AgentPanel.vue    # 外部 Agent 任务面板
    │   └── ConfirmPanel.vue  # 逐步确认面板
    ├── api/                  # API 客户端
    ├── utils/                # 工具函数（markdown/图片处理）
    └── style.css             # 全局样式

persona-菟菚.md               # 人格源文件
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| POST | `/api/health/shutdown` | 优雅关停后端 |
| GET | `/api/meta` | 工具状态 + 心情 |
| GET | `/api/sessions/{session_id}` | 会话消息 |
| POST | `/api/sessions/archive` | 归档会话 |
| GET | `/api/sessions/archives` | 归档列表 |
| GET | `/api/sessions/archives/search?q=` | 归档关键词搜索 |
| GET | `/api/sessions/archives/{archive_id}` | 归档详情 |
| GET | `/api/sessions/{session_id}/export` | 导出会话 |
| POST | `/api/chat` | SSE 流式对话（含工具进度事件） |
| POST | `/api/vision` | 识图 |
| GET | `/api/images/{name}` | 图片服务 |
| GET | `/api/persona` / `/persona/cutout` / `/persona/full` | 立绘资源 |
| GET/POST | `/api/config` | 配置读写 |
| GET | `/api/tts` | 语音朗读 |
| GET | `/api/greeting` | 久别问候 |
| GET | `/api/initiative` | 主动消息投递（取走即清） |
| GET | `/api/initiative/stream` | 主动消息 SSE 长连接 |
| POST | `/api/confirm` | 逐步确认回复 |
| GET | `/api/confirm/pending` | 待确认步骤 |
| GET/POST | `/api/agent/tasks` | 外部 Agent 任务列表/创建 |
| POST | `/api/agent/tasks/{id}/run` | 运行 Agent 任务 |
| POST | `/api/agent/tasks/{id}/confirm-step` | 确认单步 |
| POST | `/api/remote/task` | 受控远程任务 |
| GET | `/api/audit/log` | 操作日志 |
| GET/POST | `/api/mcp/servers` | 外部 MCP 服务器管理 |
| GET | `/mcp/tools` | 列出内置 MCP 工具 |
| POST | `/mcp/call` | 调用内置 MCP 工具 |
| GET | `/api/plugins` | 插件列表 |
| POST | `/api/plugins/{name}/enable` / `disable` / `reload` | 插件启停/热加载 |

## 配置项（.env）

完整示例见 `.env.example`，含注释说明。核心变量：

| 变量 | 必填 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | ✅ | OpenAI 兼容端点 |
| `LLM_API_KEY` | ✅ | LLM 密钥 |
| `LLM_MODEL` | ✅ | 模型名 |
| `LLM_PERCEPTION_*` | | 感知层独立小模型（情绪/辱骂分类，留空复用主 LLM） |
| `SEARCH_API_KEY` | | 搜索密钥（博查优先，留空自动回退 bing/ddg） |
| `IMAGE_API_KEY` | | 文生图密钥（SiliconFlow，留空关闭生图） |
| `VISION_API_KEY` | | 识图密钥（留空复用 IMAGE key） |
| `MOOD_CITY` | | 心情城市（如"北京"） |
| `MEMORY_EMBED_MODEL` | | 记忆 embedding 模型（默认 bge-m3，可换 bge-small-zh） |
| `HF_ENDPOINT` | | HuggingFace 镜像（默认 hf-mirror.com，国内友好） |
| `AGENT_REMOTE_TOKEN` | | 受控端点鉴权 token（非回环来源必填） |
| `AGENT_CODEX_*` / `AGENT_DSH_*` | | 外部 Agent 桥配置（Codex CLI / DSH） |

## 从旧版迁移

旧版 QQ bot 位于 `D:\DSH\TZtuzhan\`（NoneBot2 + NapCat），不再维护。
本仓库 `TZtuzhanAssistant` 是完全独立的 Web/桌面版。
