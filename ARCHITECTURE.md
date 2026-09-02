# 菟菚桌面助手（TZtuzhanAssistant）重构架构

## 1. 现状概览

现有代码（重构前）：
- `assistant.py`（67KB）—— 单文件 FastAPI 入口 + 内联 700+ 行 HTML/CSS/JS 前端代码
- `session_store.py`（9KB）—— SQLite 会话存储
- `maintenance.py`（6KB）—— 后台维护（checkpoint/备份/清理）
- `core/`（30 个模块，~400KB）—— 对话核心逻辑
- 前端：内联在 assistant.py 的 `_PAGE` 字符串中，纯前端无框架

重构要点：
- 后端：从单文件拆分为清晰分层结构
- 前端：从内联 HTML 重写为 Electron + Vue 3 + TypeScript
- 工具：从 ```tool``` 代码块（仅 search/weather）升级为完整 MCP 协议层
- 移除：日程(schedule)、节日(holidays)、口头禅(terms)、表达风格(style)、表情收藏(sticker)、热梗(memes) 共 6 个模块

## 2. 整体架构

```
frontend/ (Electron + Vue 3 + TS)
    │
    │ HTTP API + SSE (localhost:8801)
    ▼
backend/ (Python FastAPI + SQLite)
    │
    ├── api/          # 路由层（轻量，只做参数校验 + 转发）
    ├── core/         # 对话核心逻辑（精简后的 20 个模块）
    ├── tools/        # 工具层（MCP 协议 + 内置工具）
    ├── session/      # 会话存储
    └── maintenance/  # 后台维护
```

## 3. 目录结构

```
D:\DSH\TZtuzhanAssistant\
├── backend/                     # Python 后端
│   ├── __init__.py
│   ├── main.py                  # FastAPI 入口 + 启动
│   ├── app.py                   # FastAPI 应用工厂
│   ├── config.py                # 配置加载（从 core/config 迁移）
│   ├── api/                     # API 路由
│   │   ├── __init__.py
│   │   ├── chat.py              # POST /api/chat SSE 流式对话
│   │   ├── sessions.py          # 会话 CRUD
│   │   ├── vision.py            # POST /api/vision 识图
│   │   ├── images.py            # GET /api/images/{name} 图片服务
│   │   ├── config.py            # GET/POST /api/config 配置编辑
│   │   ├── meta.py              # GET /api/meta 工具状态
│   │   ├── greeting.py          # GET /api/greeting 久别问候
│   │   ├── tts.py               # GET /api/tts 语音朗读
│   │   └── health.py            # GET /api/health 健康检查
│   ├── core/                    # 对话核心（精简版）
│   │   ├── __init__.py
│   │   ├── pipeline.py          # 对话主流程（process 入口）
│   │   ├── llm.py               # OpenAI 客户端（流式+非流式+重试）
│   │   ├── persona.py           # 人格 prompt 构建
│   │   ├── affection.py         # 好感度系统
│   │   ├── mood.py              # 心情系统
│   │   ├── memory.py            # 长期记忆召回
│   │   ├── vector_store.py      # 语义向量记忆
│   │   ├── userdb.py            # SQLite 数据层
│   │   ├── search.py            # 联网搜索
│   │   ├── imagegen.py          # 文生图
│   │   ├── vision.py            # 识图
│   │   ├── context.py           # 上下文锚定
│   │   ├── intent.py            # 意图路由（精简）
│   │   ├── greeting.py          # 久别问候
│   │   ├── topic_memory.py      # 话题记忆
│   │   ├── triple_memory.py     # 结构化事实记忆（五元组）
│   │   ├── tasks.py             # 后台任务调度
│   │   └── log.py               # 日志
│   ├── tools/                   # 工具层（新增）
│   │   ├── __init__.py
│   │   ├── base.py              # 工具基类 + 注册机制
│   │   ├── mcp_server.py        # MCP 协议服务器（遵循 MCP spec）
│   │   ├── mcp_client.py        # MCP 客户端（连接外部 MCP 服务器）
│   │   ├── tool_loop.py         # 工具调用循环（替代旧版）
│   │   └── builtin/             # 内置工具
│   │       ├── __init__.py
│   │       ├── web_search.py    # 联网搜索（复用 core/search）
│   │       ├── web_fetch.py     # 网页抓取
│   │       ├── file_ops.py      # 文件操作（读/写/搜索）
│   │       ├── code_exec.py     # 代码执行（Python/JS 沙箱）
│   │       ├── sys_cmd.py       # 系统命令执行
│   │       └── api_bridge.py    # 外部 API 接入框架
│   ├── session/                 # 会话存储
│   │   ├── __init__.py
│   │   ├── store.py             # SQLite 会话存储（从 session_store 迁移）
│   │   └── search.py            # 跨会话全文搜索
│   ├── maintenance/             # 后台维护
│   │   ├── __init__.py
│   │   ├── loop.py              # 周期任务循环
│   │   ├── checkpoint.py        # WAL checkpoint
│   │   ├── backup.py            # 备份
│   │   └── cleanup.py           # 孤儿图片清理
│   ├── models/                  # Pydantic 数据模型
│   │   ├── __init__.py
│   │   ├── chat.py              # 对话请求/响应模型
│   │   ├── session.py           # 会话模型
│   │   ├── tool.py              # 工具调用模型
│   │   └── config.py            # 配置模型
│   └── requirements.txt
├── frontend/                    # Electron + Vue 3 + TS 前端
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── electron/
│   │   ├── main.ts              # Electron 主进程（窗口管理、托盘）
│   │   └── preload.ts           # 预加载脚本（桥接 API）
│   ├── src/
│   │   ├── main.ts              # Vue 应用入口
│   │   ├── App.vue              # 根组件
│   │   ├── style.css            # 全局样式
│   │   ├── api/                 # 后端 API 客户端
│   │   │   ├── index.ts         # 统一 API 封装
│   │   │   ├── chat.ts          # 对话 SSE 接口
│   │   │   └── sessions.ts      # 会话接口
│   │   ├── components/          # 组件
│   │   │   ├── ChatView.vue     # 对话主区域
│   │   │   ├── MessageBubble.vue # 消息气泡组件
│   │   │   ├── SessionList.vue  # 左侧会话列表
│   │   │   ├── ToolBar.vue      # 工具状态条
│   │   │   ├── MoodCard.vue     # 心情卡片
│   │   │   ├── ImageViewer.vue  # 图片灯箱
│   │   │   ├── Portrait.vue     # 菟菚立绘显示
│   │   │   ├── SettingsPanel.vue # 设置面板
│   │   │   └── InputBar.vue     # 输入栏
│   │   ├── composables/         # 组合式函数
│   │   │   ├── useChat.ts       # 对话逻辑
│   │   │   ├── useSSE.ts        # SSE 流式处理
│   │   │   └── useSession.ts    # 会话管理
│   │   ├── views/               # 页面视图
│   │   │   └── MainLayout.vue   # 主布局
│   │   └── utils/               # 工具函数
│   │       ├── markdown.ts      # Markdown 渲染
│   │       └── api.ts           # HTTP 客户端
│   └── index.html
├── data/                        # 运行时数据（SQLite / 图片）
│   ├── bot.db
│   ├── sessions.db
│   └── imgs/
├── assets/                      # 静态资源
│   └── persona.png              # 菟菚立绘
├── persona-菟菚.md               # 人格源文件
├── .env.example                 # 配置模板
├── requirements.txt             # 根目录依赖清单
├── ARCHITECTURE.md              # 本文档
└── README.md
```

## 4. 后端分层

```
┌─────────────────────────────────────────────────┐
│  api/ 路由层 (FastAPI 路由)                      │
│  - 参数校验、HTTP 状态码、JSON 序列化             │
│  - 不包含业务逻辑，只做转发                        │
├─────────────────────────────────────────────────┤
│  core/ 对话核心层                                │
│  - pipeline.py: process() 主入口                │
│  - 人格/好感度/心情/记忆/搜索/生图/识图           │
│  - 不依赖 FastAPI，纯业务逻辑                    │
├─────────────────────────────────────────────────┤
│  tools/ 工具层 (新增)                            │
│  - base.py: 工具基类 + 注册器                    │
│  - mcp_server: MCP 协议服务器                   │
│  - mcp_client: 外部 MCP 服务器客户端             │
│  - tool_loop: 工具调用循环                       │
│  - builtin/: 内置工具（文件/代码/网页/命令/API）   │
├─────────────────────────────────────────────────┤
│  session/ 会话层                                 │
│  - 会话 CRUD + 消息持久化                        │
│  - 跨会话搜索                                    │
├─────────────────────────────────────────────────┤
│  maintenance/ 维护层                              │
│  - 后台周期任务（checkpoint/备份/清理）            │
├─────────────────────────────────────────────────┤
│  models/ 数据模型层                               │
│  - Pydantic 模型定义                             │
│  - 请求/响应 schema                              │
├─────────────────────────────────────────────────┤
│  config.py / main.py 配置与启动                    │
└─────────────────────────────────────────────────┘
```

## 5. 数据流

```
用户（Electron 前端）
  │
  │ POST /api/chat (text, session_id)
  ▼
api/chat.py → session/store.py (记录用户消息)
  │
  ▼
core/pipeline.py → process(user_id, text)
  │
  ├── 1. 好感度即时规则（affection.on_message）
  ├── 2. 称呼提取（llm.extract_address）
  ├── 3. 记忆检索（memory.recall + recall_facts）
  ├── 4. 意图路由（intent.classify）
  ├── 5. 工具循环（tools/tool_loop.run）
  │     ├── 内置工具（builtin/*）
  │     └── MCP 工具（mcp_server 调用外部服务）
  ├── 6. 拼 prompt → LLM 调用
  ├── 7. 流式回调（stream_cb → SSE 推送）
  └── 8. 存档回复
  │
  ▼
api/chat.py → SSE 流式响应（前端逐字渲染）
```

## 6. 工具层设计（MCP 协议）

### 6.1 工具注册

```python
class ToolRegistry:
    """全局工具注册表。"""
    _tools: dict[str, Tool] = {}

    @classmethod
    def register(cls, tool: Tool) -> None:
        cls._tools[tool.name] = tool

    @classmethod
    def get_tool(cls, name: str) -> Tool | None
    @classmethod
    def list_tools(cls) -> list[ToolSpec]
    @classmethod
    async def execute(cls, name: str, args: dict) -> ToolResult
```

### 6.2 工具基类

```python
class Tool:
    name: str
    description: str
    input_schema: dict  # JSON Schema

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult
```

### 6.3 内置工具

| 工具名 | 描述 | 输入 |
|---|---|---|
| `web_search` | 联网搜索 | `{query: string}` |
| `web_fetch` | 抓取网页内容 | `{url: string}` |
| `read_file` | 读取文件 | `{path: string}` |
| `write_file` | 写入文件 | `{path: string, content: string}` |
| `list_dir` | 列出目录 | `{path: string}` |
| `run_code` | 执行代码（沙箱） | `{language: string, code: string}` |
| `run_command` | 执行系统命令 | `{command: string, cwd?: string}` |
| `call_api` | 调用外部 HTTP API | `{url: string, method, headers?, body?}` |

### 6.4 MCP 协议

- **MCP 服务器**：以推荐 MCP 标准 JSON-RPC 协议提供 `list_tools` / `call_tool` 端点
- **MCP 客户端**：连接外部 MCP 服务器（如 DSH 的 MCP 生态系统），自动发现并注册远程工具
- 工具调用方式：保留 ````tool``` 代码块方式（兼容现有模型行为），同时支持 Function Calling API

## 7. 迁移计划

### 第一阶段：后端骨架
1. 创建 `backend/` 目录结构（api/ core/ tools/ session/ maintenance/ models/）
2. 迁移 `config.py` → `backend/config.py`（添加类型注解，清理冗余）
3. 创建 `backend/app.py` FastAPI 应用工厂
4. 创建 `backend/main.py` 启动入口

### 第二阶段：核心模块迁移
1. 复制 core/ 到 `backend/core/`（保留现有功能不变的模块）
2. 删除被砍模块：schedule/ holidays/ terms/ style/ memes/ sticker
3. 清理依赖（删除对这些模块的 import 引用）
4. 重写 `pipeline.py`（清理冗余，类型注解，集成工具层）

### 第三阶段：工具层
1. 实现 `tools/base.py`（Tool 基类 + ToolRegistry）
2. 实现 `tools/tool_loop.py`（新版工具循环）
3. 实现内置工具（web_search/ web_fetch/ file_ops/ code_exec/ sys_cmd/ api_bridge）
4. 实现 `mcp_server.py` + `mcp_client.py`

### 第四阶段：前端
1. 初始化 `frontend/`（Vue 3 + Vite + Electron 项目）
2. 实现核心组件（ChatView/MessageBubble/SessionList/InputBar/Portrait）
3. 实现 SSE 流式对话
4. 实现 Markdown 渲染

### 第五阶段：集成测试
1. 启动后端，连接前端，端到端验证
2. 验证保留功能（对话/记忆/好感度/心情/搜索/生图/识图）
3. 验证 MCP 工具链

## 8. 记忆系统 v2（2026-09-01 重构）

记忆系统已从「SQLite + sqlite-vec + SiliconFlow embedding」重构为
「SQLite + Chroma + BGE-M3 本地 + Mem0 管理」的三层架构。

### 8.1 架构

```
┌──────────────────────────────────────────────────────────────┐
│ backend/core/memory/  记忆系统 v2 包                          │
│  ├── embedding.py      本地 embedding：BGE-M3 (sbert, CPU)    │
│  │                     回退链：bge-m3 → bge-small-zh → 哈希    │
│  ├── vector_store.py   Chroma 向量库（data/chroma/，8 分区）   │
│  │                     kind: lm/facts/triples/profile/topic/  │
│  │                           diary/summary/sticker            │
│  ├── memory_manager.py 记忆管理：Mem0 优先，自研回退           │
│  ├── long_term.py      三路融合检索：Chroma 向量 + TF-IDF +    │
│  │                     Mem0 管理记忆（去重排序 top_k）         │
│  ├── short_term.py     短期上下文（最近 30 条）                │
│  ├── fact_extractor.py 更强 LLM 事实抽取（置信度/类别/冲突调和）│
│  ├── triple_memory.py  结构化五元组（向量检索 + TF-IDF 兜底）   │
│  ├── topic_memory.py   话题记忆（kv + Chroma topic 分区）      │
│  ├── date_memory.py    特殊日子（识别 prompt + SQLite）        │
│  ├── compress.py       长会话 6 分区压缩（更强 LLM 提炼）       │
│  ├── engine.py         记忆引擎：初始化/回填/写入编排          │
│  └── migration.py      存量数据迁移（SQLite → Chroma）         │
├──────────────────────────────────────────────────────────────┤
│ 兼容薄壳（保留旧接口，调用方零改动）                           │
│  core/memory.py       → 删除（被 memory/ 包取代）             │
│  core/vector_store.py → 转发 memory.vector_store             │
│  core/triple_memory.py / topic_memory.py / date_memory.py    │
│                       → 转发 memory 同名模块                  │
└──────────────────────────────────────────────────────────────┘
```

### 8.2 关键改动

| 旧版 | 新版 |
|---|---|
| SiliconFlow embedding API（联网） | BGE-M3 本地推理（sbert，CPU，离线可用） |
| sqlite-vec（bot.db 内嵌向量表） | Chroma PersistentClient（data/chroma/） |
| TF-IDF 单路召回 | 三路融合：Chroma 向量 + TF-IDF + Mem0 |
| 无记忆管理 | Mem0（回溯/冲突解决/重要性/遗忘），失败回退自研 |
| 简单事实提取 | 更强 LLM 提取（置信度/类别）+ 冲突调和 reconcile |

### 8.3 配置（.env）

```ini
MEMORY_V2=1              # 记忆 v2 总开关（0 退回旧版）
MEMORY_MEM0=1            # Mem0 记忆管理器开关
MEMORY_EMBED_MODEL=BAAI/bge-m3   # 本地 embedding 模型
MEMORY_EMBED_FORCE=0     # 1 = 跳过本地模型强制哈希回退（调试）
```

### 8.4 依赖（requirements.txt 新增）

```
chromadb>=1.5.0          # 本地向量库
sentence-transformers>=6.0.0  # BGE-M3 本地 embedding
torch>=2.10.0            # sbert 推理后端（CPU）
mem0ai>=2.0.0            # 记忆管理框架
```

### 8.5 测试

```
python -m tests.test_memory_v2          # 向量库/检索/回忆检测/查询扩展
python -m tests.test_pipeline_scenario  # pipeline 场景（mock）
python -m tests.test_mem0               # Mem0 集成验证
python -m tests.test_recall_real        # 真实数据检索验证
python -m tests.check_data_consistency  # SQLite 与 Chroma 一致性
python -m tests.test_live_chat          # 真实 LLM 端到端对话
```