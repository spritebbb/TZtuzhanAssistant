# 菟菚 Web 助手（TZtuzhanAssistant）— 开发待办

> 目标：把菟菚做成类 DeepSeek Harness 的拟人 Web AI 助手（流式对话 + 执行任务/调工具 + 保留人格/记忆/好感度），完全脱离 QQ。
> 独立仓库：`D:\DSH\TZtuzhanAssistant\`（core 已从 TZtuzhan 复制，已验证对话链路无 nonebot 依赖，可零改动复用）

---

## ✅ 已完成

- [x] 创建独立仓库目录，复制 `core/`（llm/memory/persona/search/userdb/affection/intent/schedule/mood/config 等全部模块）、`persona-菟菚.md`、`.env.example`、`assets/`
- [x] 验证：`pipeline.process()` 的对话链路完全不依赖 nonebot/QQ，可独立运行
- [x] 建好 `.venv`（`--without-pip` + pip 引导成功）
- [x] 安装依赖：openai/fastapi/uvicorn/ddgs/zhdate/Pillow/loguru/python-dotenv/python-multipart 全部装好
- [x] `requirements.txt` 已写（精简，去 nonebot 系，含 loguru/python-multipart）
- [x] `.env` 已建（配置从 TZtuzhan 原项目复制，LLM/搜索/识图/生图 key 就绪）
- [x] 验证：core 11 模块脱离 QQ import 全部通过；真实 LLM 对话跑通（"嗯，你好。我叫菟菚。你呢，怎么称呼？"）
- [x] `core/llm.py` 加 `chat_stream()`（OpenAI stream=True 逐 chunk）
- [x] `core/pipeline.py` 加 `stream_cb` 流式回调（流式模式用"直接说"提示，避免【思考】段外泄）
- [x] `assistant.py` 主入口：FastAPI + `/` 深色对话界面 + `/api/chat` SSE 流式 + `/persona` 人设图
- [x] SSE 流式实测跑通：逐块 `piece` 推送 → `done` 收尾（菟菚回复"你好。我该怎么称呼你？"）
- [x] git init + 首个版本提交（`1483eed`）

## 📋 待办（按顺序）

### 1. ✅ 安装依赖（已完成）
### 2. ✅ 写 requirements.txt（已完成）
### 3. ✅ 验证 core 脱离 QQ import 通过（已完成）
### 4. ✅ assistant.py 主入口（已完成）

### 5. ✅ 前端 UI 打磨（已完成）
- [x] 会话历史持久化（`data/sessions.json` 后端落盘，刷新不丢；`localStorage` 记住上次会话）
- [x] 左侧会话列表（多会话新建/切换/删除，标题自动取首句，按时间倒序）
- [x] 头像/气泡动效（fadeUp 弹入）、错误重试按钮（复用气泡重发）
- [x] 工具开关指示条（搜索/天气/生图/识图/记忆，来自 `/api/meta`）

> 架构：多会话共享 `_UID="assistant-main"`（菟菚记忆/好感度跨会话连续，符合拟人单用户定位）；会话只是 UI 分组，历史由后端 `data/sessions.json` 持久化（已被 .gitignore 忽略）。

### 6. 验证与收尾
- [x] 多轮工具实测：问天气（真实襄阳数据注入）、搜新闻（联网结果）、画图（生图链路已接通：pipeline→imagegen→/api/images→前端<img>）
- [x] 架构优化 B 组：会话存储 SQLite（session_store.py，替代 sessions.json 整文件读写）+ 搜索 TTL 缓存 + 删会话清孤儿图片
- [x] 体验优化 A 组：bot 气泡 Markdown 渲染（先转义防 XSS）、停止生成按钮（AbortController 中断流式）、复制按钮 + 图片点击灯箱放大、移动端抽屉式侧栏（☰ 展开）
- [ ] 浏览器实测多轮对话 + 工具调用（问天气/搜新闻）
- [ ] 局域网访问测试（--host 0.0.0.0）
- [ ] 写 README（启动方式、端口、配置说明）
- [ ] 公网部署方案调研（内网穿透/服务器）

---

## 参考信息

- **人格文件**：`persona-菟菚.md`（坚强独立 + 腹黑毒舌 + 地狱笑话，已全面重写）
- **对话核心**：`core/pipeline.py` 的 `process(user_id, text)`（含人格/记忆/好感度/意图路由/工具循环）
- **LLM**：`core/llm.py`（OpenAI 兼容，现为非流式，需加 stream）
- **原项目**：`D:\DSH\TZtuzhan\`（QQ bot，不动；此仓库为独立 Web 助手）
- **TZtuzhan 未推送 commit**：本地 9 个（人格重写/人设图/辱骂修复等），需代理后推送并重打包部署包（与本次 Web 助手无关，另办）
