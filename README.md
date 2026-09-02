# 菟菚桌面助手（TZtuzhanAssistant）

> 类 DeepSeek Harness 的拟人 AI 助手 — **Electron 本地桌面版本**。
> 完全脱离 QQ / NoneBot，单用户 · 本机运行 · 完整人格 · 记忆 · 好感度 · MCP 工具链。

## 特性

- 🎭 **拟人人格**：毒舌腹黑、地狱笑话，人格写在 `persona-菟菚.md`
- 🧠 **长期记忆**：SQLite 存储用户画像、关系事实（五元组）、话题延续、重要日子
- 💖 **好感度系统**：昵称、关心、分享、道歉、夸奖等互动实时增减好感度
- ⛅ **心情系统**：绑定城市天气，心情随当日天气波动
- 🔎 **联网搜索**：博查 API 优先，bing/ddg 兜底；TTL 30 分钟缓存
- 🎨 **文生图**：SiliconFlow Qwen-Image，SSE 回传图片
- 👁️ **识图**：拖拽或粘贴图片，视觉模型描述内容
- 🧰 **MCP 工具链**：文件操作、代码执行、网页抓取、系统命令、外部 API 接入
- 🤖 **Codex 外部桥**：`codex_run` 可派发独立任务给本机 Codex CLI（非交互 exec 模式，每步确认）
- 💬 **流式输出**：SSE 打字机效果，Markdown 渲染，可随时停止生成
- 📂 **多会话**：SQLite 存储，共享同一人格记忆
- 🖼️ **静态立绘**：桌面窗口显示菟菚人设图

## 快速开始

```powershell
# 1. 后端：创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 2. 配置
copy .env.example .env
# 编辑 .env，至少填 LLM_API_KEY

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
├── main.py                   # 启动入口
├── app.py                    # FastAPI 应用工厂
├── config.py                 # 配置加载
├── api/                      # API 路由层
│   ├── chat.py               # SSE 流式对话
│   ├── sessions.py           # 会话 CRUD
│   ├── vision.py             # 识图
│   ├── images.py             # 图片服务
│   ├── meta.py               # 工具状态
│   ├── config_api.py         # 配置编辑
│   ├── greeting.py           # 久别问候
│   ├── tts.py                # 语音朗读
│   └── health.py             # 健康检查
├── core/                     # 对话核心
│   ├── pipeline.py           # 对话主流程
│   ├── llm.py                # LLM 客户端
│   ├── persona.py            # 人格注入
│   ├── affection.py          # 好感度
│   ├── mood.py               # 心情
│   ├── memory.py             # 记忆系统
│   ├── userdb.py             # SQLite 数据层
│   ├── search.py             # 联网搜索
│   ├── imagegen.py           # 文生图
│   ├── vision.py             # 识图
│   └── ...                   # 更多模块
├── tools/                    # 工具层
│   ├── base.py               # 工具注册表
│   ├── tool_loop.py          # 工具调用循环
│   ├── mcp_server.py         # MCP 协议服务器
│   └── builtin/              # 内置工具
│       ├── web_search.py     # 联网搜索
│       ├── web_fetch.py      # 网页抓取
│       ├── file_ops.py       # 文件操作
│       └── code_exec.py      # 代码/命令执行
├── session/                  # 会话存储
│   └── store.py              # SQLite 会话持久化
├── maintenance/              # 后台维护
│   └── loop.py               # 周期任务
└── models/                   # 数据模型
    ├── chat.py / session.py
    ├── tool.py / config.py

frontend/                     # Electron + Vue 3 + TS 前端
├── package.json
├── vite.config.ts
├── electron/
│   ├── main.ts               # Electron 主进程
│   └── preload.ts            # 预加载脚本
└── src/
    ├── App.vue               # 根组件
    ├── components/           # 组件
    │   ├── ChatView.vue      # 对话主界面
    │   ├── MessageBubble.vue
    │   ├── SessionList.vue   # 会话侧栏
    │   ├── Portrait.vue      # 立绘显示
    │   ├── ToolBar.vue       # 工具状态条
    │   └── SettingsPanel.vue # 设置面板
    ├── api/                  # API 客户端
    ├── utils/                # 工具函数
    └── style.css             # 全局样式

data/                         # 运行时数据
├── bot.db                    # 用户/记忆/好感度
├── sessions.db               # 会话/消息
└── imgs/                     # 生图产物

persona-菟菚.md               # 人格源文件
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/meta` | 工具状态 + 心情 |
| GET/POST/PATCH/DELETE | `/api/sessions` | 会话 CRUD |
| POST | `/api/chat` | SSE 流式对话 |
| POST | `/api/vision` | 识图 |
| GET | `/api/images/{name}` | 图片服务 |
| GET/POST | `/api/config` | 配置编辑 |
| GET | `/api/tts` | 语音朗读 |
| GET | `/api/greeting` | 久别问候 |
| GET | `/mcp/tools` | 列出 MCP 工具 |
| POST | `/mcp/call` | 调用 MCP 工具 |

## 配置项（.env）

| 变量 | 必填 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | ✅ | OpenAI 兼容端点 |
| `LLM_API_KEY` | ✅ | LLM 密钥 |
| `LLM_MODEL` | ✅ | 模型名 |
| `SEARCH_API_KEY` | | 搜索密钥（博查优先） |
| `IMAGE_API_KEY` | | 文生图密钥（SiliconFlow） |
| `VISION_API_KEY` | | 识图密钥（默认同 LLM） |
| `MOOD_CITY` | | 心情城市（如"襄阳"） |

## 从旧版迁移

旧版 QQ bot 位于 `D:\DSH\TZtuzhan\`（NoneBot2 + NapCat），不再维护。
本仓库 `TZtuzhanAssistant` 是完全独立的 Web/桌面版。
