# 菟菚桌面助手（TZtuzhanAssistant）

> 类 DeepSeek Harness 的拟人 AI 助手 — **本地桌面 / 浏览器版本**。
> 完全脱离 QQ / NoneBot，单用户 · 本机运行 · 完整人格 · 记忆 · 好感度 · 插件化工具链。

![Release](https://img.shields.io/github/v/release/spritebbb/TZtuzhanAssistant)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![Python](https://img.shields.io/badge/python-3.11%20~%203.13-green)
![Backend](https://img.shields.io/badge/backend-FastAPI-009688)
![Frontend](https://img.shields.io/badge/frontend-Electron%20%2B%20Vue3-4FC08D)

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

---

# 🚀 部署指南

三种运行形态按需选择：

| 形态 | 适合人群 | 需要安装 | 下载 |
|---|---|---|---|
| **① 一键部署包 · 轻量版**（推荐） | 只想快速用起来 | 仅 Python 3.11~3.13 | [Deploy zip（约 5 MB）](https://github.com/spritebbb/TZtuzhanAssistant/releases/latest) |
| **① 一键部署包 · 大杯版（large）** | 语义记忆效果优先 | 同上，首次启动多下载 1.2GB 模型 | [Deploy-Full large zip（约 5 MB）](https://github.com/spritebbb/TZtuzhanAssistant/releases/latest) |
| **② 桌面安装包** | 想要 Electron 桌面窗口 | Python 3.11~3.13 + 手动启动后端 | [Setup exe（约 80 MB）](https://github.com/spritebbb/TZtuzhanAssistant/releases/latest) |
| **③ 源码部署** | 开发者 / 想改代码 | Python + Node.js | `git clone` |

> 两种一键部署包唯一区别是记忆 embedding 模型默认值：轻量版 `BAAI/bge-small-zh-v1.5`（约 100MB）；大杯版（文件名带 `large`，GitHub 资产名不支持中文）`BAAI/bge-m3`（约 1.2GB，首次启动自动下载，中文语义检索效果最好）。这只是 `.env` 默认配置，装好后随时可手动改。

**通用前置要求**（三种方式都需要）：

- Windows 10 / 11
- **Python 3.11 ~ 3.13**（[python.org 下载](https://www.python.org/downloads/)，安装时务必勾选 **"Add python.exe to PATH"**；过旧或过新的版本可能导致 `torch` / `sentence-transformers` 安装失败）
- 可联网（首次安装依赖约 1~2 GB，之后日常流量很小）
- 现代浏览器（Chrome / Edge）

**唯一必填配置**：一个 OpenAI 兼容的 LLM API Key（如 [DeepSeek](https://platform.deepseek.com)），其余全部可选、留空即关闭对应功能。

---

## 方式一：一键部署包（推荐，全程 5 分钟）

不需要 Node.js、不需要 Electron 打包环境。后端会自动托管前端页面，双击脚本即可。

### 步骤

1. **下载**：到 [Releases 页面](https://github.com/spritebbb/TZtuzhanAssistant/releases/latest) 下载部署包：
   - `TZtuzhanAssistant-Deploy-vX.X.X.zip` — 轻量版（embedding 用 bge-small-zh-v1.5，约 100MB）
   - `TZtuzhanAssistant-Deploy-Full-vX.X.X-large.zip` — 大杯版 / large（embedding 用 bge-m3，约 1.2GB，语义检索效果最好）
2. **解压**到任意目录（建议英文路径，如 `D:\Tuzhan`）。
3. **双击 `Start-Tuzhan.bat`**。脚本会自动完成：
   - 检测 / 创建 `.venv` 虚拟环境；
   - 安装 `requirements.txt` 全部依赖（首次较慢，1~2 GB，请耐心等待）；
   - 从 `.env.example` 生成 `.env`；
   - 若 `LLM_API_KEY` 为空，自动用记事本打开 `.env` 让你填写；
   - 启动后端（`http://127.0.0.1:8801`）并打开浏览器页面。
4. **填写 API Key**：在弹出的记事本里把 `LLM_API_KEY=sk-你的真实Key` 填好，保存关闭，回到黑窗口按任意键继续。
5. 浏览器自动打开菟菚界面，开始聊天 🎉

### 日常使用

- **启动**：双击 `Start-Tuzhan.bat`（后端已在运行时会直接复用并打开页面）
- **停止**：双击 `Stop-Tuzhan.bat`，或关闭任务栏上名为 `Tuzhan-backend` 的最小化窗口
- **数据备份**：聊天记录、记忆、图片都存在包内 `data/` 目录，删除/更新包前请先备份整个目录
- 立绘、人格、技能、插件都已内置，无需额外配置

> 脚本找不到 Python？重装 Python 并勾选 PATH 后**重开**启动脚本即可。

---

## 方式二：桌面安装包（Electron 窗口）

1. 到 [Releases 页面](https://github.com/spritebbb/TZtuzhanAssistant/releases/latest) 下载 `TZtuzhanAssistant-Setup-vX.X.X-win-x64.exe` 并安装。
2. **注意：安装包仅含前端 Electron 壳，不内置 Python 运行时**（后端依赖含 torch/chromadb 体积过大）。首次使用前先手动启动后端：

```bat
:: 在仓库源码目录执行（见方式三的 1~3 步）
.venv\Scripts\python backend\main.py --host 127.0.0.1 --port 8801
```

3. 打开桌面端「菟菚桌面助手」，它会自动探测 `http://127.0.0.1:8801`：后端已在运行则直接连接，没有则提示你先启动后端。

> 日常使用其实更推荐方式一（不需要手动管后端）；方式二适合想要独立桌面窗口 + 立绘常驻的场景。

---

## 方式三：源码部署（开发者）

```powershell
# 1. 拉取源码
git clone https://github.com/spritebbb/TZtuzhanAssistant.git
cd TZtuzhanAssistant

# 2. 后端：创建虚拟环境并安装依赖（首次 1~2 GB）
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 3. 配置
copy .env.example .env
# 编辑 .env，至少填 LLM_API_KEY（详见下方「配置项」）

# 4. 启动后端（默认 127.0.0.1:8801）
python -m backend.main

# 5. 前端（二选一）
#    a) 只用浏览器访问：先构建一次，之后后端自动托管页面
cd frontend; npm install; npm run build; cd ..
#    b) 开发模式（Vite + Electron 热重载）
cd frontend; npm install; npm run dev

# 6. 打包 Windows 安装包
cd frontend; npm run dist:win
```

- 后端入口支持 `python -m backend.main --host 127.0.0.1 --port 8801 --debug`
- 后端检测到 `frontend/dist` 存在时会自动托管前端页面，浏览器访问 `http://127.0.0.1:8801` 即可使用，无需单独跑前端 dev server
- 打包部署包（生成 `deploy/` 一键包）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_deploy.ps1
```

---

## 局域网访问与安全

- 默认只监听本机回环地址（`127.0.0.1`），**局域网设备无法访问**，这是安全默认。
- 如需局域网访问：手动用 `--host 0.0.0.0` 启动后端，并阅读 `.env.example` 中 `AGENT_REMOTE_TOKEN` / `AGENT_ALLOWED_HOSTS` 的说明配置鉴权 token。未配置 token 时，受控端点（写操作、MCP、Agent 桥、插件管理）对非本机来源一律拒绝。

## 常见问题

**Q1：提示 "Python not found"**
安装 Python 3.11~3.13 并勾选 "Add python.exe to PATH"，然后重新运行启动脚本。

**Q2：依赖安装失败 / 超时**

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**Q3：首次对话较慢 / 后台下载数据**
记忆系统首次会下载 embedding 模型。部署包默认使用较小的 `BAAI/bge-small-zh-v1.5`（约 100MB，经 `HF_ENDPOINT=https://hf-mirror.com` 国内镜像下载）；想要更好的语义检索可改 `BAAI/bge-m3`（约 1.2GB），或设 `MEMORY_EMBED_FORCE=1` 走零依赖哈希回退。

**Q4：页面打不开 / 端口被占用**

```bat
netstat -ano | findstr 8801
```

关闭占用 8801 端口的程序后重试，或换端口启动后端。

**Q5：LLM 请求报代理错误**
`.env` 中设置 `LLM_PROXY=off` 强制直连（本机有失效代理时）。

---

## 项目结构

```
backend/                      # Python 后端
├── main.py                   # 启动入口（--host/--port，默认 127.0.0.1:8801）
├── app.py                    # FastAPI 应用工厂（路由挂载 + 中间件 + 工具注册 + 静态托管）
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
├── data/                     # 运行时数据（bot.db/sessions.db/截图等，不入库）
└── models/                   # 数据模型

frontend/                     # Electron + Vue 3 + TS 前端
├── package.json
├── vite.config.ts            # Electron 形态配置
├── vite.web.config.ts        # 纯浏览器形态配置（不启 Electron 壳）
├── electron/
│   ├── main.ts               # Electron 主进程（通知/后端拉起/进程管理）
│   └── preload.ts            # 预加载脚本
├── public/                   # 静态资源（sw.js 离线缓存/图标/PWA manifest）
├── release/                  # electron-builder 打包产物（不入库）
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

scripts/                      # 工具脚本
├── build_deploy.ps1          # 一键部署包打包脚本
└── deploy_assets/            # 部署模板（.env.example / 使用说明）

deploy/                       # 一键部署包产物（见 Releases）
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

完整示例见 [.env.example](.env.example)（含注释说明）；部署包内为 `scripts/deploy_assets/.env.example`。核心变量：

| 变量 | 必填 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | ✅ | OpenAI 兼容端点（默认 `https://api.deepseek.com/v1`） |
| `LLM_API_KEY` | ✅ | LLM 密钥 |
| `LLM_MODEL` | ✅ | 模型名（默认 `deepseek-chat`，任意 OpenAI 兼容模型均可） |
| `LLM_PROXY` | | 设 `off` 强制直连（本机有失效代理时） |
| `LLM_PERCEPTION_*` | | 感知层独立小模型（情绪/辱骂分类，留空复用主 LLM） |
| `PERSONA_FILE` | | 人格文件路径（默认 `persona-菟菚.md`） |
| `SEARCH_API_KEY` | | 搜索密钥（博查优先，留空自动回退 bing/ddg） |
| `IMAGE_API_KEY` | | 文生图密钥（SiliconFlow，留空关闭生图） |
| `VISION_API_KEY` | | 识图密钥（留空复用 IMAGE key） |
| `MOOD_CITY` | | 心情城市（如"北京"） |
| `MEMORY_EMBED_MODEL` | | 记忆 embedding 模型（部署包默认 bge-small-zh-v1.5，可换 bge-m3） |
| `HF_ENDPOINT` | | HuggingFace 镜像（默认 hf-mirror.com，国内友好） |
| `AGENT_REMOTE_TOKEN` | | 受控端点鉴权 token（非回环来源必填） |
| `AGENT_ALLOWED_HOSTS` | | 受控端点允许的 Host 白名单 |
| `AGENT_CODEX_*` / `AGENT_DSH_*` | | 外部 Agent 桥配置（Codex CLI / DSH） |
