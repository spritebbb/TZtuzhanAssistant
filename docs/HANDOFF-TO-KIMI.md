# 菟菚桌面助手 —— Kimi 接手/并行协作交接报告

> 生成时间：2026-09-04
> 生成人：WorkBuddy（上一手智能体，D2 阶段退出）
> 接收人：Kimi（执行智能体，与 Codex 同角色、可并行接手）
> 状态基线：工作区干净（`git status` 无改动），最新提交 `0f659a8`（D5），测试 **39/39 全绿**

---

## 〇、先读这一段：并行协作边界（与 Codex 版报告的关键差异）

你和 Codex 被安排为**同角色并行执行**。为确保不互相覆盖，遵守三条纪律：

1. **改文件前先 `git status` + `git log --oneline -3`**，确认上一手没动过你要改的文件。撞车以"最新提交/谁先落盘谁为准"，落盘后立即 commit。
2. **任务分域，别抢同一文件**。见第九节建议的分工——若你和 Codex 都领到了同一模块，先协商谁主写，另一个只做评审/互补，**绝不双写同一 .py**。
3. **每轮必带隔离测试 + commit**。你落地的每个模块都要有独立 `tests/test_*.py`，跑绿再交，别指望对方替你兜底。

技术基线 / 已完成清单 / 工程坑 / 剩余项 与 Codex 版**同源**，本节之外你读到的事实都是同一份，可放心当作共同上下文。

---

## 一、这是什么项目

一个**桌面 AI 女友养成应用**（代号「菟菚」），强调**拟人深度**而非工具属性：

- 多档关系状态、精力/情绪系统、会主动说话、会发图、有自己的日记
- 记忆是核心：事实、长期记忆、对话、日记全进向量库，聊久了会"记得你"
- 技术：Electron + Vue3 前端，Python(FastAPI) 后端，本地运行
- 已出安装包：`frontend/release/菟菚桌面助手 Setup 2.1.0.exe`

架构见 `ARCHITECTURE.md`，人设见根目录 `persona-菟菚.md`（**改动任何行为前先读它，别破坏人格一致性**）。

---

## 二、当前整体状态（一句话）

**路线图 17/25 完成**，最新提交 `0f659a8`（D5 多模型路由 + 成本面板），**测试 39/39 全绿**，安装包 v2.1.0 已出。D2 本次只做了技术预研、**一行代码未落**，工作区干净，可安全接手。

| 基线项 | 值 |
|---|---|
| 最新提交 | `0f659a8` D5 |
| 路线图完成 | 17/25 |
| 测试 | 39 项全绿 |
| 安装包 | `frontend/release/菟菚桌面助手 Setup 2.1.0.exe`（93MB NSIS） |
| 待办拍板项 | C4 好感度 / C5 仪表盘 / D3 共同活动（**用户要亲自拍板**） |
| 待办自主项 | D2 RAG 知识库（已预研，可直接开工）、D8 移动端（卡公网部署） |

---

## 三、技术栈与目录速览

- **后端**：Python(FastAPI)，入口 `backend/app.py`，核心逻辑在 `backend/core/`
- **前端**：Vue3 + TypeScript + Vite，`frontend/src/`；Electron 打包配置在 `frontend/`
- **记忆/向量**：ChromaDB，封装 `backend/core/memory/vector_store.py`
- **配置**：环境变量，`backend/core/config.py` 统一读取，模板 `.env.example`
- **依赖**：见 `requirements.txt`（openai/httpx/fastapi/uvicorn/...）；虚拟环境 `.venv/`
- **测试**：pytest，入口 `tests/test_suite_runner.py`

```
backend/core/            # pipeline/daily/initiative/llm/persona/mood/affection/...
backend/core/memory/     # 记忆系统：vector_store/recall/daily/compress/...
backend/api/             # FastAPI 路由（usage.py、keepsake.py、diary.py、memory_admin.py...）
frontend/src/components/ # Vue 面板（UsagePanel.vue、DiaryPanel.vue、MemoryPanel.vue...）
tests/                   # pytest，每轮迭代带独立隔离测试
data/                    # 运行时数据（db/chroma/logs）
docs/                    # CODE-REVIEW-*.md、DEPLOYMENT-OPTIONS.md、HANDOFF-TO-CODEX.md 等
deliverables/            # 每轮迭代报告
EVOLUTION-ROADMAP.md     # 路线图（逐项验收 + 版本号）← 主进度文件
```

---

## 四、已完成能力清单（17/25）

逐轮提交 + 隔离测试，验收细节在 `EVOLUTION-ROADMAP.md`。近期模块：

| 模块 | 内容 | 提交 |
|---|---|---|
| C3 | 纪念日预谋 + 昼夜背景自动切换 | `593d287` |
| C6 | 约定与跟进（promises 表，daily 提炼 + 到点主动问） | `6731f3f` |
| C7 | 记忆纠偏（facts 删改 API + LLM 仲裁真删 + 当场认错） | `3908ef8` |
| D6 | 边界场景（深夜 23-5 emo 守护 + 健康边界/催睡） | `a478de7` |
| D4 | 纪念册导出（自包含 HTML 温室纸感主题） | `6ad5d14` |
| D7 | Electron 打包 v2.1.0（NSIS） | `4805a9e` |
| D1 | 人格微演化（user_terms：口头禅/黑话 ≥2 次才注入） | `4805a9e` |
| D5 | 多模型路由 + 成本面板 | `0f659a8` |

更早的 A1-A4 / B1-B5 / C1-C2（TTS、主动发图、梦境、表情包、人格 eval、多状态立绘、可解释性面板、精力哄好、日记研究）见路线图。完整七轮详表在 `deliverables/iteration-report-2026-09-04.md`。

---

## 五、D2 预研结论（本次会话只做了这个，未写代码）

D2 = **RAG 知识库**：让菟菚能"读"投喂的文档（pdf/txt/md），语义检索相关段落并在对话中自然引用。

**向量库现状（`backend/core/memory/vector_store.py`）：**
- kind 白名单 L19：`_KINDS = {"lm","facts","triples","profile","topic","diary","summary","sticker","mem"}`
- 已支持任意 kind 分 collection + 语义检索，核心接口：`add(user_id,kind,record_id,text,extra)`(L151)、`search(user_id,query,top_k,kind=None)`(L188，跨分区)、`delete/count/clear/stats/migrate_user_id`
- **RAG 只需新增一种 kind（如 `kb`）+ 解析入库 + 检索注入，无需动向量库本体**
- 向量库按 `user_id` 过滤，天然多用户隔离

**依赖缺口：** `pypdf` **未安装**（实测 ModuleNotFoundError）。装到 `.venv`：
```
.venv/Scripts/python.exe -m pip install pypdf
```
txt/md 无需三方库。

**语义检索注入点（对话主流程）：**
- `backend/core/pipeline.py` L11 `from .memory import recall, recall_facts, short_term_messages`
- L683 `recall(user_id,text)`、L684 `recall_facts(...)` — 记忆注入入口；RAG「相关段落注入」可仿此在 L678 起的记忆区加一条（本地文件检索不走云端 LLM，更快）

**D2 待定设计点（开工前需与用户或 Codex 对齐）：**
1. 文档来源：前端拖拽上传 → 存 `data/documents/`？还是读本地文件夹？（建议前者，贴近现有 API 风格）
2. 分块策略：建议 500-800 字符重叠分块（决定检索精度）
3. 触发方式：仅用户明确问文档时才检索省 token，还是每轮都带
4. 前端：要不要给"知识库管理页"（看/删已投喂文档）

---

## 六、剩余工作全景（未做项）

路线图尾部未勾选项：
- **[ ] C4 好感度玩法闭环** —— 用户明确"等你拍板"，**不要擅自开工**
- **[ ] C5 养成仪表盘** —— 同上，**等拍板**
- **[ ] D3 共同活动** —— 同上，**等拍板**
- **[ ] D2 RAG 知识库** —— 用户口头批准，**可自主开工**（第五节预研已就绪）
- **[ ] D8 移动端推送** —— **卡公网部署**（见 `docs/DEPLOYMENT-OPTIONS.md`），无公网地址前无法推进

**Codex 版报告的优先级建议是「先做 D2」。你若并行，请先和 Codex 协商 D2 主写归属；若你领到别的域（C4/C5/D3 均需用户拍板除外），可做技术评审或等 Codex D2 落地后做审查/人格一致性校验。**

---

## 七、⚠️ 关键工程约定与坑（接手必读）

### 1. pipeline.py 用 config 必须"函数内局部导入"
`backend/core/pipeline.py` **不在顶层 import config**，每个用到处函数内 `from .config import config`。D5 曾违反导致 NameError 连锁放倒 7 项测试（`0f659a8` 修复）。改此文件务必遵守。

### 2. 用项目自己的 Python 环境
一律用 `.venv/Scripts/python.exe`（本项目 venv），**不要用系统 python**。依赖在 `requirements.txt`，新增依赖用 `pip install` 装进 `.venv` 并同步 `requirements.txt`。

### 3. 测试套件入口与流程
- 全量：`pytest tests/test_suite_runner.py -q`（聚合 `tests/` 各模块）
- 改动后先 `py_compile` 相关 .py 再跑套件，防连锁
- 目标状态：跑出 **39/39 或更多全绿**

### 4. 前端改完过 vue-tsc + vite build
```
cd frontend && npx vue-tsc --noEmit && npx vite build
```
产物在 `frontend/dist/`（hash 化）。类型不净跑不过 build。

### 5. 改后端后要重启后端进程才生效
后端不热加载。改动 daily/initiative/pipeline/llm 等后，实际验证/测试前需重启后端（日志在根目录 `backend_stdout.log` / `backend_stderr.log`，启动见 README / `start.bat`）。可用测试绕过重启做逻辑验证。

### 6. .env.example 与 .env 同步改
配置改动同时落 `.env.example`（模板）与真实 `.env`，别只改一边。

### 7. 新表/字段记得进 reset 覆盖清单
新增 DB 表（如 D5 的 `usage_log`、C6 的 `promises`）要同步加进 `backend/core/reset.py` 和 `backend/core/userdb.py` 的 reset 清单，否则"重置数据"残留脏表。

### 8. 每轮迭代交付约定
每落地一模块：独立 `tests/test_*.py` → 跑绿 → 更新 `EVOLUTION-ROADMAP.md`（勾选 + 版本号）→ 写 `deliverables/` 迭代报告 → git commit。**别跳过测试直接交。**

---

## 八、D5 变更速览（改到这块先看这里）

D5 提交 `0f659a8`：
- `backend/api/usage.py`（新增）— `/api/usage/summary`
- `backend/core/llm.py` — `chat/chat_stream` 加 `model=` 覆盖；流式 `include_usage` 精确用量，无 usage 本地估算兜底
- `backend/core/config.py` — 新增 `llm_model_strong` 等
- `backend/core/pipeline.py` — 主回复启发式路由强模型（L62 `_needs_strong_model`：写作/代码/长文）
- `backend/core/userdb.py` — 新增 `usage_log` 表
- `frontend/src/components/UsagePanel.vue`（新增）+ `frontend/src/api/usage.ts` — 「养她的账本」
- `.env.example` — `LLM_MODEL_STRONG` / `LLM_PRICE_*`

---

## 九、给 Kimi 的下一步建议

1. **先跑一次全量测试确认基线**：`pytest tests/test_suite_runner.py -q` → 应 39/39 全绿
2. 读 `EVOLUTION-ROADMAP.md` + `persona-菟菚.md`
3. **与 Codex 对齐分工**（若 Codex 已开工 D2，你转为评审/互补，勿抢同一 .py）
4. 若你主做 D2：按第五节 → 装 pypdf → 加 `kb` kind → 分块入库 API → pipeline L678 注入 → 前端上传/管理页 → `tests/test_knowledge_base.py` → 更新路线图 → commit
5. **C4/C5/D3 等拍板项不要碰**；D8 需公网部署资源

---

_需要人工/用户决策：C4/C5/D3 玩法方向；D2 的文档来源与前端形态（可与 Codex 协商后报给用户）；D8 需公网部署资源。_
