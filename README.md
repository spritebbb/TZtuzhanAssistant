<p align="center">
  <img src="assets/persona.png" alt="菟菚桌面助手立绘" width="260" />
</p>

<h1 align="center">菟菚桌面助手</h1>

<p align="center">
  <strong>有记忆、有性格、有情绪、能与你共同成长的本地 AI 伙伴</strong><br />
  桌面端 / 浏览器端 · 单用户本机运行 · 人格、记忆、关系与工具链一体化
</p>

<p align="center">
  <a href="https://github.com/spritebbb/TZtuzhanAssistant/releases/latest"><img src="https://img.shields.io/github/v/release/spritebbb/TZtuzhanAssistant?display_name=tag&style=for-the-badge&color=2f855a" alt="GitHub Release" /></a>
  <img src="https://img.shields.io/badge/Windows-10%2F11-0078D4?style=for-the-badge&logo=windows" alt="Windows 10/11" />
  <img src="https://img.shields.io/badge/Python-3.11--3.13-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11 to 3.13" />
  <img src="https://img.shields.io/badge/FastAPI-%2B%20Vue%203-009688?style=for-the-badge&logo=fastapi" alt="FastAPI and Vue 3" />
</p>

<p align="center">
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-v310-更新亮点">v3.1 亮点</a> ·
  <a href="#-核心能力">核心能力</a> ·
  <a href="#-能力演示新手教程">能力演示</a> ·
  <a href="#-mcp-扩展接你自己的工具">MCP 扩展</a> ·
  <a href="#-部署指南">部署指南</a> ·
  <a href="#-局域网访问与安全">局域网安全</a> ·
  <a href="#-配置项env">配置</a>
</p>

---

## 📦 v3.1.0 已发布

| 推荐下载 | 适合谁 | 下载 |
|---|---|---|
| **轻量部署包** | 想尽快开始使用；首次下载约 100 MB 记忆模型 | [下载 ZIP](https://github.com/spritebbb/TZtuzhanAssistant/releases/download/v3.1.0/TZtuzhanAssistant-Deploy-v3.1.0.zip) |
| **大杯部署包** | 更重视中文语义记忆；首次下载约 1.2 GB BGE-M3 模型 | [下载 Large ZIP](https://github.com/spritebbb/TZtuzhanAssistant/releases/download/v3.1.0/TZtuzhanAssistant-Deploy-Full-v3.1.0-Large.zip) |

> 两个包都不需要 Node.js。解压后双击 `Start-Tuzhan.bat`，填写一个 OpenAI 兼容的 `LLM_API_KEY` 即可开始。

<p align="center">
  <a href="https://github.com/spritebbb/TZtuzhanAssistant/releases/tag/v3.1.0"><strong>查看完整 Release 说明 →</strong></a>
</p>

## ✨ v3.1.0 更新亮点

v3.1 把菟菚从「有状态的伙伴」推进到「**能派活、能自己收尾的 Agent**」，并补齐了长期运行需要的治理与可观测能力：

- 🤖 **Agent 全生命周期**：聊天里一句「帮我分几步做 X」即可建任务（自动拆成多步计划）；支持**定时执行**、**失败重试**、**任务产物落盘**（`workspace/agent-reports/`），并在任务面板逐步确认每一步。
- 🌐 **MCP 标准协议接入**：客户端支持 Streamable HTTP 与旧版 HTTP+SSE 自动探测，可接生态里的真实 MCP 服务器（Playwright 浏览器自动化、Context7 实时文档已实测）；配套 **stdio→HTTP 桥**，让只讲 stdio 的服务器也能接；工具**按需注入**，工具表不会随服务器增加而膨胀。
- 👀 **监控 Agent**：让她盯着某个网页，内容变化时主动告诉你——只比对内容指纹、不保存网页正文。
- 🗣️ **主动引擎会做事**：到点且需要准备的约定，先派工具把材料备好再汇报（查不到就如实说，绝不编）。
- 🎓 **内置能力演示（新手教程）**：说「新手教程」或点「更多 → 能力演示」，她按顺序**真实执行**七步能力展示，每步停下等你决定是否继续。
- 🧩 **共享与授权**：知识/产物可显式「分享给某个角色」（默认全部隔离，首期只读），撤销即时生效。
- 📈 **本地质量统计与演化日志**：延迟/失败规则/来源选择等聚合计数（默认本地、可关可清）；表达层参数小幅演化可撤销、可重算。
- 🛠️ **工具循环加固**：同签名二次熔断、32KB 结果预算、结构化错误（不泄堆栈/密钥）、LLM 客户端缓存有界。
- 🗂️ **文档与共创**：网页/EPUB 摄入、世界观与角色共创（含结构化大纲）、观察日志、阅读地图。

## ✨ v3.0.0 更新亮点

v3.0 相比 v2.5 完成了「关系连续性」大版本演进（M8 + M9 前三波次），菟菚从"有记忆的助手"升级为"有内在状态的伙伴"：

- 💞 **关系双维模型**：好感度升级为 **信任 × 亲密** 双维度，8 个子阶段时刻自然展开；关系会随真实互动经历季节与版本演化。
- 🎭 **七情绪状态机**：喜悦、信任、恐惧、惊讶、悲伤、厌恶、愤怒七情绪独立衰减；菟菚对你的态度由情绪 × 信任 × 亲密合成，同样的话在不同关系状态下有不同的语气。
- 🌸 **真实事件驱动**：纪念日与自然季节调制心情；久别重逢以真实生活事件为叙事来源，无事件时诚实留白、不编造"你去哪了"。
- 🤝 **双向约定与偏好**：对称约定（她也有承诺要守）+ 用户偏好教学（"别叫我全名"这类规矩说一遍就记住）。
- 📖 **有出处的知识**：知识库观点带来源片段与相关性注入，失效自动撤销；联网结论经多源 2+1 求证后才说出口。
- 🗡️ **工具安全合同**：工具执行具备取消、步数上限与副作用去重；关键操作逐步确认。
- 🕰️ **时间感**：日程状态机 + 跨进程小时滴答，菟菚知道现在是什么时段、什么季节。
- 🏛️ **M8 长期回望**：写给未来的信、30/100/365 天关系快照、双视角叙事、阶段封存与告别、"不同版本的我们"检查点、梦境与平行可能（虚构严格隔离）。
- 🛡️ **可靠性底座**：输出卫生门禁（所有可见回复统一质检）、每日可验证备份（三个 SQLite 库 + 媒体 + 人格 + 知识源）、八任务键模型路由、人格行为签名评测。

## ✨ 核心能力

| | |
|---|---|
| 🎭 **人格热切换**<br />直接加载 `.md` 人格卡；每套人格的会话、记忆、知识库和界面设置相互隔离。 | 🧠 **可信长期记忆**<br />SQLite + 本地向量检索；每条记忆有来源与置信度，可纠正、可衰减、可固定保留。 |
| 🧰 **工具与 Agent**<br />插件热加载、MCP 接入、工具循环与逐步确认，关键动作可控。 | 💬 **实时流式对话**<br />SSE 打字机输出、工具进度、图片生成与随时停止。 |
| 💖 **关系与情绪**<br />信任×亲密双维关系、七情绪状态机、季节与纪念日调制，形成连续的关系历史。 | 🛡️ **本机优先安全**<br />默认仅回环监听；LAN 请求受 token、Host 白名单与 SSRF 防护约束。 |
| 🤖 **Agent 任务**<br />聊天派活 / 定时 / 重试 / 产物落盘 / 逐步确认；子代理并行分头干活。 | 🌐 **MCP 生态**<br />标准协议 + stdio 桥 + 按需注入，可接浏览器自动化、实时文档等外部服务器。 |

## 🎓 能力演示（新手教程）

第一次用不知道她能做什么？两种方式看一遍：

- **聊天里说一句**：「新手教程」「演示一下」「带我看看你会什么」——她会按顺序**真实执行**七步演示，每步做完停下等你决定是否继续；
- **点界面入口**：顶部「更多 → 能力演示」，每一步都有一句可直接发送的原话。

演示顺序（从日常到硬核）：

| # | 展示 | 建议原话 |
|---|---|---|
| 1 | 长期记忆 | 记住一件事：我明天要去看牙医。 |
| 2 | 联网查实时信息 | 查一下今天武汉的天气。 |
| 3 | 动手操作本机 | 在工作区建一个 demo.txt 写上今天日期再读回来。 |
| 4 | **并行子代理** | 帮我比较 Python 和 Node.js 做后端，从性能、生态、学习成本三方面分析。 |
| 5 | **浏览器自动化（MCP）** | 用浏览器打开 example.com，看看页面上写了什么。 |
| 6 | 查实时文档（MCP） | 帮我查一下 fastapi 的文档，怎么定义带查询参数的路由？ |
| 7 | 监控 Agent | 帮我盯着某个页面，有变化告诉我。 |

第 4、5 步是 Agent 能力主秀。详细步骤与排查见 [docs/AGENT-TOUR.md](docs/AGENT-TOUR.md)。

## 🔌 MCP 扩展（接你自己的工具）

菟菚支持接入 **标准 MCP 服务器**，用外部工具扩展她的能力边界：

- 客户端走标准协议（Streamable HTTP 为主，旧版 HTTP+SSE 自动回退），生态里的服务器可直接接；
- 只讲 stdio 的服务器（如 Playwright MCP 默认模式）用内置的 **stdio→HTTP 桥**接进来；
- MCP 工具**按需注入**：只有本轮对话/技能/任务命中触发词时才注入该服务器的工具，工具表不会无限膨胀；
- 外部工具默认归类为「外部 + 需确认」，每次调用前征求同意。

```bat
:: 以 Playwright MCP 为例：先起桥（内部会拉起 npx @playwright/mcp）
scripts\start-mcp-playwright.bat
:: 再启动菟菚（start.bat 会先等桥就绪再起后端）
start.bat
```

然后到设置页「MCP 服务器」确认工具已注册（如 `playwright::browser_navigate`）。
完整说明（含回环放行开关、关键词配置、排查表）见 [docs/MCP-STANDARD.md](docs/MCP-STANDARD.md)。

> 只建议接「本地工具做不到」的服务器（浏览器自动化、GitHub/Notion、数据库等）；与本地工具重复的（filesystem/shell/memory/搜索）不建议接入。

## 🚀 快速开始

1. 从上方下载 **轻量部署包** 或 **大杯部署包**。
2. 解压到英文路径（如 `D:\Tuzhan`），双击 `Start-Tuzhan.bat`。
3. 首次运行时在自动打开的 `.env` 中填写 `LLM_API_KEY`，保存后返回终端继续。
4. 浏览器打开 `http://127.0.0.1:8801` 后即可聊天。

> 需要 Python 3.11 ~ 3.13；首次安装依赖和记忆模型时需要联网，请耐心等待。

---

## 🧩 功能一览

- 🎭 **人格热切换**：点击顶部人格名称打开人格替换助手，加载与原卡相同格式的 `.md` 文件；旧卡保存在 `data/personas/`
- 🧠 **长期记忆**：SQLite 存储用户画像、关系事实（五元组）、话题延续、重要日子；本地 BGE 向量语义检索；每条事实带来源、置信度与验证时间，误记可纠正、琐碎记忆自然衰减、重要记忆可固定保留
- 💞 **信任×亲密关系**：真实互动按事件记账，单维每日限额防刷；阶段与子阶段由双维派生，好感度兼容为 `min(trust, intimacy)`
- 🎭 **七情绪状态机**：独立衰减的情绪状态 + 情绪×关系态度矩阵，深夜/低精力自动降低追问意愿
- 🤝 **对称约定**：你们互相许下的承诺都有状态机（她失约也会被记账）；开放约定自动出现在成长总览
- 📣 **用户偏好教学**：告诉菟菚一条规矩（如"别用表情包回我"），她记住并在后续表达中遵守
- ⛅ **心情与时间感**：绑定城市天气，纪念日与自然季节调制情绪基线；日程状态机感知工作日/休息日节奏
- 🔎 **联网搜索**：博查 API 优先，bing/ddg 兜底；TTL 缓存；动态结论经独立站点 2+1 求证
- 📚 **知识库**：文档→分块→观点→来源四层结构，观点带来源片段，内容失效自动撤销，支持导出恢复
- 🎨 **文生图**：SiliconFlow Qwen-Image，SSE 回传图片
- 👁️ **识图**：拖拽或粘贴图片，视觉模型描述内容，推理过程不外泄到聊天
- 🧰 **插件化工具链**：工具全部以 `plugins/*.py` 插件形式加载，支持热加载（无需重启）、每步确认机制、外部 MCP 服务器接入
- 🤖 **外部 Agent 桥**：`codex_run` / `dsh_run` 可派发独立任务给本机 Codex CLI / DSH（非交互 exec 模式，每步确认）
- 💬 **流式输出**：SSE 打字机效果 + 工具执行进度实时推送，Markdown 渲染，可随时停止
- 📂 **独立会话 + 归档**：每套人格拥有独立的当前会话、归档、关系状态、长期记忆与知识库
- 🕐 **主动性引擎**：久未聊且关系够近时主动开口，统一仲裁器协调天气/约定/纪念日/未完成心事等来源，支持桌面通知
- 📬 **写给未来的我们**：按日期/目标/事件解锁的信件
- 📸 **关系快照与检查点**：30/100/365 天确定性快照；"不同版本的我们"命名检查点，升级前后行为可比较
- 👥 **双视角叙事**：共同经历双方各留一段感想，观点不入事实召回
- 🏛️ **阶段封存与重逢**：关系阶段封存只导出不删除，附告别信；久别重逢以真实生活事件为来源，无事件诚实留白
- 💭 **梦境与平行可能**：共同世界观与"另一种可能"产物，`<fiction_story>` 虚构隔离，只有显式收藏的能留下
- 🛡️ **输出卫生门禁**：所有用户可见生成出口（聊天/主动消息/日记/观点/重逢）共用同一套质检与脱敏
- 💾 **每日可验证备份**：三个 SQLite 库、媒体、人格与知识源自动快照，schema 升级前额外备份并可校验完整性

人格名称默认从 Markdown 的第一个标题读取。卡片也可选用以下 front matter，让主题与音色首次加载时自动建立；之后可在 UI 中修改并随人格保存：

```md
---
name: Luna
subtitle: 月夜里的陪伴者
theme: light
voice: zh-CN-XiaoyiNeural
---

# Luna
这里开始写人格正文……
```

---

## 🚀 部署指南

三种运行形态按需选择：

| 形态 | 适合人群 | 需要安装 | 下载 |
|---|---|---|---|
| **① 一键部署包 · 轻量版**（推荐） | 只想快速用起来 | 仅 Python 3.11~3.13 | [下载 v3.1.0 ZIP](https://github.com/spritebbb/TZtuzhanAssistant/releases/download/v3.1.0/TZtuzhanAssistant-Deploy-v3.1.0.zip) |
| **① 一键部署包 · 大杯版（Large）** | 语义记忆效果优先 | 同上，首次启动多下载 1.2GB 模型 | [下载 v3.1.0 Large ZIP](https://github.com/spritebbb/TZtuzhanAssistant/releases/download/v3.1.0/TZtuzhanAssistant-Deploy-Full-v3.1.0-Large.zip) |
| **② 桌面安装包** | 想要 Electron 桌面窗口 | Python 3.11~3.13 + 手动启动后端 | [Setup exe](https://github.com/spritebbb/TZtuzhanAssistant/releases/latest) |
| **③ 源码部署** | 开发者 / 想改代码 | Python + Node.js | `git clone` |

> 两种一键部署包唯一区别是记忆 embedding 模型默认值：轻量版 `BAAI/bge-small-zh-v1.5`（约 100MB）；大杯版（文件名带 `Large`）`BAAI/bge-m3`（约 1.2GB，首次启动自动下载，中文语义检索效果最好）。这只是 `.env` 默认配置，装好后随时可手动改。

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

1. **下载**：到 [v3.1.0 Releases 页面](https://github.com/spritebbb/TZtuzhanAssistant/releases/tag/v3.1.0) 下载部署包：
   - `TZtuzhanAssistant-Deploy-vX.X.X.zip` — 轻量版（embedding 用 bge-small-zh-v1.5，约 100MB）
   - `TZtuzhanAssistant-Deploy-Full-vX.X.X-Large.zip` — 大杯版 / Large（embedding 用 bge-m3，约 1.2GB，语义检索效果最好）
2. **解压**到任意目录（建议英文路径，如 `D:\Tuzhan`）。
3. **双击 `Start-Tuzhan.bat`**。脚本会自动完成：
   - 检测 / 创建 `.venv` 虚拟环境；
   - 安装 `requirements.txt` 全部依赖（首次较慢，1~2 GB，请耐心等待）；
   - 从 `.env.example` 生成 `.env`；
   - 若 `LLM_API_KEY` 为空，自动用记事本打开 `.env` 让你填写；
   - 启动后端（`http://127.0.0.1:8801`）并打开浏览器页面。
4. **填写 API Key**：在弹出的记事本里把 `LLM_API_KEY=sk-你的真实Key` 填好，保存关闭，回到黑窗口按任意键继续。
5. 浏览器自动打开菟菚界面，开始聊天 🎉

### 从 v2.x / v3.0 升级

直接用 v3.1 部署包覆盖旧目录前，**先备份包内 `data/` 目录**（聊天记录、记忆、关系状态都在里面）。首次启动会自动把旧 schema 迁移到 v22（信任/亲密双维、情绪状态、事件链等新表会自动创建并回填），旧好感度会自动换算为初始信任与亲密值。

### 日常使用

- **启动**：双击 `Start-Tuzhan.bat`（后端已在运行时会直接复用并打开页面）
- **停止**：双击 `Stop-Tuzhan.bat`，或关闭任务栏上名为 `Tuzhan-backend` 的最小化窗口
- **数据备份**：聊天记录、记忆、图片都存在包内 `data/` 目录；schema 升级前程序会把 SQLite 库快照到 `data/backups/schema-*` 并做完整性校验，每日另有可验证备份，日常仍建议在删除/更新包前备份整个目录
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
- 运行测试（后端聚合回归 + 前端单测）：

```powershell
.venv\Scripts\python -m pytest tests/
cd frontend; npm test
```

- 打包部署包（生成 `deploy/` 一键包）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_deploy.ps1
```

---

## 局域网访问与安全

- 默认只监听本机回环地址（`127.0.0.1`），**局域网设备无法访问**，这是安全默认。
- 如需局域网访问：手动用 `--host 0.0.0.0` 启动后端，并阅读 `.env.example` 中 `AGENT_REMOTE_TOKEN` / `AGENT_ALLOWED_HOSTS` 的说明配置鉴权 token。未配置 token 时，受控端点（写操作、MCP、Agent 桥、插件管理）对非本机来源一律拒绝。
- 跨设备私用、公网临时演示、固定域名和云服务器的选型与安全清单见 [远程访问与公网部署方案](docs/DEPLOYMENT-OPTIONS.md)。
- 所有用户可见的生成内容在送达前经过统一卫生检查；工具执行有取消、步数上限与副作用去重；联网结论经多源求证后才呈现。

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

**Q6：从 v2.x 升级后好感度/记忆还在吗**
在。首次启动会自动迁移 schema 到 v22：旧好感度换算为初始信任×亲密值，全部记忆、会话、快照保留。升级前请先备份 `data/` 目录；程序也会在 schema 升级前自动做一次快照备份到 `data/backups/schema-*`。

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
│   ├── llm.py                # LLM 客户端（主模型 + 感知层小模型 + 任务路由）
│   ├── perception.py         # 语义感知（情绪/辱骂/关心等分类）
│   ├── persona.py            # 人格注入 + 人格切片编译
│   ├── emotion_state.py      # 七情绪状态机（衰减/修复/门控）
│   ├── affection.py          # 信任×亲密双维关系 + 阶段派生
│   ├── mood.py               # 心情（天气 + 日历调制）
│   ├── calendar_modulation.py# 纪念日与自然季节调制
│   ├── conversation_rhythm.py# 对话节奏生命周期
│   ├── event_chains.py       # 最小事件链引擎
│   ├── user_preferences.py   # 用户偏好教学与解析
│   ├── fact_decay.py         # 琐碎记忆自然衰减
│   ├── fact_lifecycle.py     # 事实生命周期（来源/置信度/级联清理）
│   ├── memory/               # 记忆系统（事实/话题/五元组/日期记忆 + 压缩）
│   ├── userdb.py             # SQLite 数据层（schema v22）
│   ├── search.py             # 联网搜索 + 多源求证
│   ├── knowledge.py          # 知识库（文档/分块/观点/来源）
│   ├── imagegen.py           # 文生图
│   ├── vision.py             # 识图
│   ├── initiative.py         # 主动性引擎（统一仲裁器 + 必要性门 + 先做事再汇报）
│   ├── greeting.py           # 久别问候逻辑
│   ├── watchers.py           # 监控 Agent（网页变化监视，只存哈希）
│   ├── demo_tour.py          # 内置能力演示脚本（唯一数据源）
│   ├── domain_trust.py       # 五域可依赖程度（L05）
│   ├── relationship_style.py # 长期关系气质（L03）
│   ├── aesthetic_preferences.py # 共同审美与房间陈列（L07）
│   ├── shared_resources.py   # 可选择共享与授权（L16）
│   ├── learning_pipeline.py  # 可审核的学习管线（§17.2）
│   ├── persona_evolution.py  # 表达层演化日志（P3-05）
│   ├── expression_policy.py  # 表达必要性与注意力（§17.1）
│   ├── attention_state.py    # 注意力主题衰减（§17.1）
│   └── ...                   # 更多模块
├── tools/                    # 工具层
│   ├── base.py               # 工具注册表
│   ├── tool_loop.py          # 工具调用循环（原生 Function Calling + 文本回退）
│   ├── hardening.py          # 工具循环加固（熔断/预算/结构化错误）
│   ├── service.py            # 工具轮次调度
│   ├── confirm.py            # 每步确认钩子
│   ├── mcp_client.py         # 标准 MCP 客户端（Streamable HTTP + HTTP+SSE）
│   ├── mcp_stdio_bridge.py   # stdio → Streamable HTTP 桥
│   ├── mcp_server.py         # 内置 MCP 协议服务器（/mcp/*）
│   └── builtin/              # 内置工具（仅记忆系统；其余已插件化）
├── plugins/                  # 插件（web_search/web_fetch/file_ops/code_exec 等，热加载）
│   ├── loader.py             # 插件发现/加载/卸载/热加载
│   └── context.py            # 插件钩子上下文
├── agent/                    # Agent 任务
│   └── session.py            # 多步计划/定时/重试/产物落盘 + 外部 Agent 桥
├── session/                  # 会话存储
│   └── store.py              # SQLite 会话/消息/归档持久化
├── maintenance/              # 后台维护（周期任务/时间滴答/备份/快照）
├── skills/                   # 技能目录
├── data/                     # 运行时数据（SQLite 库/截图/备份等，不入库）
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
    │   ├── MemoryPanel.vue   # 记忆管理（来源/置信度/纠偏）
    │   ├── AgentPanel.vue    # 外部 Agent 任务面板
    │   └── ConfirmPanel.vue  # 逐步确认面板
    ├── api/                  # API 客户端
    ├── utils/                # 工具函数（markdown/图片处理）
    └── style.css             # 全局样式

scripts/                      # 工具脚本
├── build_deploy.ps1          # 一键部署包打包脚本
└── deploy_assets/            # 部署模板（Start/Stop 脚本 / init_database / 使用说明）

deploy/                       # 一键部署包产物（见 Releases）
persona-菟菚.md               # 人格源文件
```

## 配置项（.env）

完整示例见 [.env.example](.env.example)（含注释说明）；部署包内为 `scripts/deploy_assets/.env.example`。核心变量：

| 变量 | 必填 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | ✅ | OpenAI 兼容端点（默认 `https://api.deepseek.com/v1`） |
| `LLM_API_KEY` | ✅ | LLM 密钥 |
| `LLM_MODEL` | ✅ | 模型名（默认 `deepseek-chat`，任意 OpenAI 兼容模型均可） |
| `LLM_PROXY` | | 设 `off` 强制直连（本机有失效代理时） |
| `LLM_PERCEPTION_*` | | 感知层独立小模型（情绪/辱骂分类，留空复用主 LLM） |
| `LLM_TASK_ROUTES` | | 按任务键路由不同模型（八任务键，旧配置兼容） |
| `PERSONA_FILE` | | 人格文件路径（默认 `persona-菟菚.md`） |
| `SEARCH_API_KEY` | | 搜索密钥（博查优先，留空自动回退 bing/ddg） |
| `IMAGE_API_KEY` | | 文生图密钥（SiliconFlow，留空关闭生图） |
| `STICKER_*` | | 自制表情包开关、概率、最小消息间隔与收藏上限 |
| `VISION_API_KEY` | | 识图密钥（留空复用 IMAGE key） |
| `MOOD_CITY` | | 心情城市（如"北京"） |
| `TZTUZHAN_DATA_DIR` | | 数据目录（默认项目根 `data/`；可用于测试或多实例隔离） |
| `MEMORY_EMBED_MODEL` | | 记忆 embedding 模型（部署包默认 bge-small-zh-v1.5，可换 bge-m3） |
| `HF_ENDPOINT` | | HuggingFace 镜像（默认 hf-mirror.com，国内友好） |
| `AGENT_REMOTE_TOKEN` | | 受控端点鉴权 token（非回环来源必填） |
| `AGENT_ALLOWED_HOSTS` | | 受控端点允许的 Host 白名单 |
| `AGENT_CODEX_*` / `AGENT_DSH_*` | | 外部 Agent 桥配置（Codex CLI / DSH） |

## 开发状态

- 当前版本：**v3.1.0**（schema v40）
- 后端聚合回归 **141/141**、前端 Vitest **90/90**、vue-tsc 与生产构建通过（2026-09-09 全量实跑）
- 长期路线见 [docs/TECH-PLAN.md](docs/TECH-PLAN.md)；架构总览见 [ARCHITECTURE.md](ARCHITECTURE.md)；插件开发见 [docs/PLUGIN-DEVELOPMENT.md](docs/PLUGIN-DEVELOPMENT.md)
- 欢迎提 Issue 与 PR；提交请保持小切片、一个主题一个提交

## 许可

个人项目，仅供学习交流使用。
