# 菟菚桌面助手（TZtuzhanAssistant）架构

> 更新日期：2026-09-18（schema v42）。本文档描述**当前实际结构**。
> 历史上这里曾是一份「重构计划」，写着「core 精简后 20 个模块」「移除日程/口头禅/
> 表情收藏等 6 个模块」——那些模块后来全部按需复活，文档却一直没跟着改，
> 于是架构文档描述的是一个不存在的系统。重写时以代码为准，并加上「如何自查」一节。

## 1. 整体架构

```
frontend/ (Electron + Vue 3 + TS / 也可纯浏览器)
    │
    │ HTTP API + SSE (默认 127.0.0.1:8801，局域网需 token)
    ▼
backend/ (Python FastAPI + SQLite + Chroma)
    │
    ├── api/          路由层：只做参数校验 + 转发（48 个模块 / 224 条路由）
    ├── core/         对话核心（122 个模块）
    ├── tools/        工具层：MCP 客户端/服务端 + 工具循环 + 加固
    ├── plugins/      插件加载器（热加载）
    ├── session/      会话存储
    ├── storage/      数据目录、加密容器、迁移引擎
    └── maintenance/  后台维护（周期任务/时间滴答/备份/快照）
plugins/            工具插件（web_search / file_ops / code_exec / watch 等）
skills/             技能目录（按 trigger 匹配，命中即注入「干活姿势」）
```

## 2. 一轮对话的数据流

这是全项目最核心的路径，也是历史上最脆弱的地方——它曾是一个 1560 行的单函数
（``pipeline._process_locked``），内部平铺 90 个内联 ``try/except``。现在按职责拆成
「编排 + 四个专职模块」：

```
POST /api/chat (SSE)
        │
        ▼
backend/core/pipeline.py          ← 只负责编排：定序、分支、调用 LLM
        │
        ├─► core/turn_effects.py          写副作用（关系入账/教学/惰性提炼/派活）
        ├─► core/turn_context_gather.py   读上下文（记忆/知识/活动/事件/搜索）
        ├─► core/turn_prompt.py           组装 system 注入
        └─► core/effect_ledger.py         旁路失败登记 + 进程级统计
        │
        ▼
core/llm.py (流式 / 非流式 / 原生 Function Calling)
        │
        ├─► tools/tool_loop.py + tools/service.py   工具循环（熔断/预算/确认钩子）
        └─► core/output_hygiene.py + stream_hygiene.py   输出卫生（防内部协议外泄）
        │
        ▼
core/userdb.py (SQLite, schema v42)  ← 权威数据
core/memory/ (Chroma 向量 + TF-IDF + Mem0)  ← 派生缓存，可重建
```

### 2.1 为什么这样拆

- **`pipeline.py` 只编排**：新增能力落在 `turn_prompt` 的一个具名函数里，而不是往
  `_process_locked` 追加第 91 个 `try/except`。这是「阻止函数继续膨胀」的机制，
  不是审美偏好。
- **写副作用与读上下文分开**：前者改变状态（失败必须记账），后者只影响本轮背景
  （失败降级成空值）。两类失败的处理方式不同，混在一起会两边都做不好。
- **失败必须可观测**：`EffectLedger` 保留「不抛出」的降级行为，但把失败计入
  `effect_stats()`，挂在 `GET /api/meta` 的 `effect_stats` 字段上。
  没有这一步，「回复照常」和「某路静默降级」在外部完全无法区分。

## 3. 分层与依赖纪律

| 层 | 允许依赖 | 禁止 |
|---|---|---|
| `api/` | `core/`、`tools/` | 直接读写 SQLite、内联业务逻辑 |
| `core/` | `core/`、`storage/` | 依赖 `api/` |
| `tools/` | `core/`（只读）、`plugins/` | 绕过确认钩子执行高危动作 |
| `plugins/` | `backend.tools.base` | 直接写 `data/` |

**已知债务**：`core/` 122 个模块之间有大量函数内 import（全后端 879 处），
是系统性回避循环依赖的结果。这不是风格选择，而是依赖图失控的症状；
新增模块时应优先把共享逻辑下沉到独立模块，而不是继续加函数内 import。

## 4. 数据与状态

- **权威**：`data/bot.db`（SQLite，schema v42）。所有关系、记忆、活动、事件都在这。
- **派生**：`data/chroma/`（向量索引）。可随时重建，**不是**真相来源。
- **加密**：P3-04 支持整库加密（SQLCipher + HKDF 分域派生 + AES-GCM 文件容器）。
  迁移是显式状态机（`storage/migration.py`），明文目录的删除必须由用户确认，
  且找不到明文目录时**报错而不是假装成功**。
- **隔离**：`TZTUZHAN_DATA_DIR` 可把整套数据目录挪走，测试与多实例都靠它。

## 5. 测试与验证

- 后端：`tests/` 下 148 个可独立运行的脚本，由 `tests/test_suite_runner.py` 接入
  pytest（`pytest tests/` → 172 个用例）。每个脚本独占 `TZTUZHAN_DATA_DIR`。
- 前端：`frontend/src/**/__tests__`（25 个文件 / 115 个用例，vitest）+ 15 个
  Playwright 关键路径用例。
- CI：`.github/workflows/regression.yml` 在后端/测试/前端变更时自动跑，
  让「全绿」由机器产出，而不是靠记忆。

### 5.1 已知的测试债务

`test_suite_runner` 把每个脚本当子进程跑、只看退出码，因此：

- 一个脚本里 40 条断言挂掉，只报「这个文件没过」，没有细粒度报告；
- 每个脚本都要重新解释器启动 + 重新加载 embedding，全量一轮约 6 分钟；
- 没有 fixture 生命周期，测试之间靠数据目录隔离而非事务回滚。

新写测试时优先用 pytest 原生风格（`test_*.py` 里的独立函数 + fixture），
只有需要真实 pipeline 端到端时才继续用脚本风格。

## 6. 如何自查（防止文档再次漂移）

```powershell
# schema 版本：文档应与此一致
Select-String -Path backend\core\userdb.py -Pattern '_SCHEMA_VERSION'

# 后端用例数
python -m pytest tests/ --collect-only -q | Select-Object -Last 1

# 前端用例数
cd frontend; npm test

# core 模块数
(Get-ChildItem backend\core -Recurse -Filter *.py -File |
  Where-Object { $_.FullName -notmatch '__pycache__' }).Count

# pipeline 里还剩多少静默降级
Select-String -Path backend\core\pipeline.py -Pattern 'except Exception' | Measure-Object
```

## 7. 前端结构

```
frontend/src/
├── App.vue                 根组件：面板开关、应用锁、专注模式
├── components/             ChatView / MessageBubble / SettingsPanel /
│                           ActivityPanel / CornerPanel / AgentPanel / ...
├── api/                    后端接口封装（每域一个文件）
├── state/                  visualState（与 /api/meta 的 visual_state 对齐）
└── utils/                  markdown / 图片 / 焦点管理
```

前端是**瘦客户端**：人格、记忆、关系、活动状态只在后端保存，前端不持有真相。
