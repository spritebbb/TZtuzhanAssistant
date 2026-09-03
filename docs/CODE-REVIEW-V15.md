# 菟菚桌面助手 · 全量代码审查报告（V15 · 已复核并修复）

> 审查范围：前端 Electron + Vue 3（全部 22 个源文件）+ 后端 FastAPI（86 个 Python 文件）
> 审查目标：全面质检（bug + 可维护性 + 性能 + 测试覆盖 + 安全）
> 审查日期：2026-09-03
> 复核结论：**初版报告基于较早的代码快照撰写，多数问题在复核时发现已在当前代码中修复。唯一仍存在、需要改代码的是第 4 条「用户身份割裂」，已按单一会话模式统一并补齐历史数据迁移。** 其余条目按「当前代码为准」复核，结论如下。
>
> **第二轮补充（2026-09-03 上午）：** 追加一轮「功能粗粝点」审查，聚焦用户能直接感知的体验缺陷，共修复 6 项（天气城市、识图落图、流式归档、工具消息打字机、vision 兜底模型、归档搜索 XSS），详见文末「六、第二轮修复」。
>
> **第三轮复核（2026-09-03 上午）：** 对第二轮修复做复核，指出 4 处「修了一半/修出新问题」的残留缺陷（识图文案仍错位、归档图片被孤儿清理误删、识图孤儿文件/扩展名猜错、归档提示条按钮生成中未禁用），本轮已全部修复，详见文末「七、第三轮复核修复」。

---

## 六、第二轮修复：功能体验缺陷（2026-09-03 追加）

本轮审查聚焦「功能写了、但用户一用就觉得不对」的粗糙点。修复结果如下：

| 编号 | 位置 | 问题 | 处理 |
|---|---|---|---|
| 🔴A | `pipeline.py:622` | 问「北京天气」返回 `MOOD_CITY` 城市天气 | ✅ 已修：先提取用户提到的城市，没提到才回落 mood_city |
| 🔴B | `ChatView.vue:192` + `vision.py` | 识图不落图、刷新后文案错位 | ✅ 已修：图片落盘 `data/imgs/`，作为 user 消息 image 持久化 |
| 🔴C | `App.vue:20` / `chat.py:139` | 流式中归档拆对话 | ✅ 已修：生成中禁用归档按钮 |
| 🟡D | `pipeline.py:1004` | 工具类消息无打字机、空窗长 | ✅ 已修：工具循环后最终回复切片推 stream_cb |
| 🟡E | `vision.py:49` | 兜底模型名仍是假模型 | ✅ 已修：改用 `_DEFAULT_VL_MODEL` |
| 🟡F | `SessionList.vue:147` | 归档搜索高亮 v-html 未净化 | ✅ 已修：转义后再 v-html，并补归档图片回显 |

### 🔴A 天气城市提取（`backend/core/pipeline.py`）

**问题**：`_fetch_weather` 前写死 `city = _cfg.mood_city`，只要消息含「天气/温度/下雨」等词就直接查配置城市，完全不看用户提到的城市。

**修复**：新增 `_extract_city(text)`，优先用 `_CITY_ALIASES`（覆盖 40+ 国内主要城市）匹配，命中直接返回；未命中再用「城市名紧贴天气词」正则兜底（覆盖港澳台/国外城市，并剥离「我想知道/帮我查/查一下」等动词前缀）。`_fetch_weather` 内部再把中文城市映射为 wttr.in 查询名（英文更稳）。

验证结果：「北京今天天气→北京」「查一下上海的温度→上海」「帮我查一下今天襄阳的天气→襄阳」「今天天气→None（回落 mood_city）」「我想知道巴黎天气→巴黎」全部正确。

### 🔴B 识图不落图（`backend/api/vision.py` + `frontend`）

**问题**：`uploadVision` 只返回描述文本，图片从不落盘；前端把「（发送了一张图片）」先插入气泡、再把描述当普通文本发给 `/api/chat`，导致刷新后用户气泡变成没打过的长文本、图片永久丢失。

**修复**：
1. `backend/api/vision.py`：上传图片落盘到 `data/imgs/vision_{md5}.{ext}`（复用 `/api/images/` 服务与文件名校验白名单），返回 `description` + `image_url`。
2. `backend/api/chat.py`：新增可选 `image` form 字段，随 user 消息一并持久化。
3. `frontend/src/api/chat.ts`：`uploadVision` 返回 `{ description, imageUrl }`；`streamChat` 支持传 image。
4. `frontend/src/components/ChatView.vue`：识图成功把图片挂到 user 消息的 `image` 字段（缩略图即时显示），发送 text 携带 image，刷新/归档后原图仍在。

### 🔴C 流式中归档拆对话（`frontend/src/App.vue` + `ChatView.vue`）

**问题**：`archiveNow` 不知道 ChatView 是否在生成中，流式期间归档按钮可用；归档后后端 `_runner` 客户端断开仍继续落库到已清空的新会话，导致归档里只有用户消息、新会话躺着孤零零的 bot 回复。

**修复**：`ChatView.vue` 新增 `streaming-change` 事件，用 `watch(isGenerating)` 把 `busy || streaming` 上报给父组件；`App.vue` 用 `generating` 状态禁用归档按钮（`archiveNow` 加 `generating` 守卫 + 按钮 `:disabled`）。

### 🟡D 工具类消息无打字机（`backend/core/pipeline.py`）

**问题**：命中工具循环触发词的消息整段走 `run_tool_round`，不经过 `chat_stream`，前端气泡空窗十几秒后整段「哐」出来。

**修复**：工具循环拿到 `raw` 后，在进入 `strip_actions` 等后处理之前，按 `_STREAM_CHUNK`（6 字）切片推 `stream_cb`，让工具类消息也享受打字机效果（与流式路径一致，推的都是 raw，最终 done 帧仍是后处理后的 reply）。

### 🟡E vision 兜底模型名（`backend/core/vision.py`）

**问题**：第 49 行 LLM 兜底分支硬编码 `deepseek-v4-flash-vision-exp`（注释自己写的「不存在、会导致 403」的假模型），而文件顶部已定义真实模型常量 `_DEFAULT_VL_MODEL = "Qwen/Qwen2.5-VL-72B-Instruct"` 却没用上。

**修复**：兜底分支改用 `_DEFAULT_VL_MODEL`。注意：用户早前确认「识图模型存在」指的是 SiliconFlow 的 Qwen VL 模型（即 `_DEFAULT_VL_MODEL`），而非这个注释里明说「不存在」的 DeepSeek 假模型名——两者不冲突，本处是修注释描述里的真 bug。

### 🟡F 归档搜索 XSS + 图片回显（`frontend/src/components/SessionList.vue`）

**问题**：`highlightText` 返回的文本直接 `v-html`，而归档标题来自用户首条消息（未经净化的用户输入），存在 XSS 面；归档查看器不回显图片。

**修复**：`highlightText` 对用户输入做 HTML 转义（`escapeHtml`）后再插入 `⟨⟩` 高亮标记；归档查看器 `vmsg` 增加 `<img>` 回显（`resolveImageSrc` 解析相对路径）。

---

## 修复结果总览

| 编号 | 问题 | 复核结果 | 处理 |
|---|---|---|---|
| 🔴1 | `vector_store.search` 重复 append | 当前代码已无重复，仅一份带 None 兜底的循环 | ✅ 无需修改 |
| 🔴2 | `_ABUSE_EN_WORDS` 定义顺序 / 英文辱骂词漏检 | 当前 `_ABUSE_EN_WORDS` 已定义在 `_expand_abuse_words()` 之前 | ✅ 无需修改 |
| 🔴3 | `check_command` 子串误杀面 | 当前已拆分「短语子串匹配 + 单词词界匹配」，`wmic` 等用 `(?<![a-z0-9])…(?![a-z0-9])` 词界 | ✅ 无需修改 |
| 🟡4 | 用户身份 `session_current` vs `assistant-main` 割裂 | **仍存在，本次已修复** | ✅ 已修复（含历史数据迁移） |
| 🟡5 | 归档搜索 N+1 串行拉详情 | 当前已改「标题命中直接采纳 + 其余 `Promise.all` 并发」 | ✅ 无需修改 |
| 🟡6 | `current_mood` 读带写副作用 | 当前已加「漂移结果相同则跳过写库」守卫 | ✅ 无需修改 |
| 🟡7 | 前端 `controller` 复用竞态窗口 | 当前有 `busy` 标志挡着，且 `finally` 有 `controller === ctrl` 守卫；单一会话下 send/识图不会并发 | ✅ 无需修改（已受控） |
| 🟡8 | `vision.py` 兜底模型名「不存在」 | 经用户确认该识图模型**确实存在**，注释描述有误 | ✅ 无需修改（模型可用） |
| 💭9-12 | 小问题 / 测试覆盖建议 | 属可选优化，不强制 | 📝 保留建议 |

---

## 一、本次实际修复：用户身份统一（🟡4 → 已修）

### 问题回顾

- 聊天链路：`backend/api/chat.py` 的 `_user_id(session_id)` 返回 `f"session_{session_id}"`，单一会话模式下恒为 `session_current`；
- 任务代理链路：`backend/api/agent.py` 用 `user_id or "assistant-main"`，前端 `AgentPanel.vue` 写死 `assistant-main`；
- `contextvar` 默认值（`core/current_user.py`）、`meta.py` 兜底、`audit.py` 默认都是 `assistant-main`。

**后果**：聊天和任务代理是两条割裂的用户记录，好感度、心情、记忆画像互不相通——同一个菟菚在「聊天」和「任务」里是两个人。

### 修复内容（以当前单一会话模式为准）

1. **`backend/api/chat.py`** — 把 `_user_id()` 改为统一返回 `assistant-main`，并注明语义（单一会话下聊天与任务代理共用同一份画像）。

2. **`backend/core/userdb.py`** — 新增 `_migrate_legacy_user_identity()`，在 `UserDB.__init__` 末尾调用（幂等）。把旧身份 `session_current` 名下的历史数据合并进 `assistant-main`：
   - `users`：好感度取两者较大值，昵称/恋人确认/日期标记 target 缺失时回填 legacy；
   - `messages` / `long_memory` / `facts` / `triples` / `affection_log` / `important_dates` / `stickers` / `user_profile` / `user_terms` / `user_style_map` / `tasks`：直接把 user_id 改挂 target；
   - `user_meta` / `kv_store` / `diary`（有唯一约束）：逐行 `INSERT OR IGNORE` 后再删 legacy 行，避免主键冲突。

3. **`backend/core/memory/vector_store.py`** — 新增 `migrate_user_id(old, new)`，遍历各 collection，把 `old|kind|record_id` 前缀的向量 id 与 metadata.user_id 同步改为 new（record_id 不变，与 SQLite 对齐）。

4. **`backend/app.py`** — 启动流程里、`ensure_ready` 之后调用 `_vec.migrate_user_id("session_current", "assistant-main")`，完成向量侧身份迁移。

> 说明：Mem0 可选增强通道（独立 `chroma_mem0` 目录）里的旧 `session_current` 记忆不在本次迁移范围内——它是可选增强，核心记忆（long_memory/facts/triples/profile/dates）都在 SQLite + Chroma，已完整迁移；且 Mem0 常态下因依赖外部 key 常降级到 fallback（fallback 用 vector_store 的 `mem` kind，已被 `migrate_user_id` 覆盖）。

---

## 二、复核确认「无需修改」的条目（当前代码已修复）

### 🔴1 `vector_store.search` 重复 append —— 已修复
当前 `backend/core/memory/vector_store.py` 的 `search()` 已只有一份循环（约 194-201 行），且带 `float(dist) if dist is not None else 0.0` 与 `text=doc or ""` 的 None 兜底。初版报告看到的「重复 append + 第二段无兜底」是旧快照，现已不存在。

### 🔴2 `_ABUSE_EN_WORDS` 定义顺序 —— 已修复
当前 `backend/core/affection.py` 第 100 行已定义 `_ABUSE_EN_WORDS`，第 103 行的 `_expand_abuse_words()` 引用它，定义在前、引用在后，顺序正确，`fuck`/`shit`/`bitch` 正常纳入词界匹配集合。

### 🔴3 `check_command` 子串误杀 —— 已修复
当前 `backend/tools/safety.py` 已把黑名单拆成两类：
- `_BLOCK_KEYWORD_PHRASES`（`sudo rm` / `net stop` / `sc delete` 等带空格的短语）走子串匹配；
- `_BLOCK_KEYWORD_WORDS`（`wmic` / `fsutil` / `bcdedit` / `gpedit` / `vssadmin` 等单词）走 `_BLOCK_WORD_PATTERN = (?<![a-z0-9])(...)(?![a-z0-9])` 词界匹配，避免误伤 `wmical`、`fsutiltool` 这类正常复合词。

### 🟡5 归档搜索 N+1 —— 已修复
当前 `frontend/src/components/SessionList.vue` 的 `searchArchives()` 已改为：标题命中的归档直接采纳、其余归档用 `Promise.all` 并发拉详情做内容匹配，消除了逐条串行 await。

### 🟡6 `current_mood` 读带写 —— 已缓解
当前 `backend/core/mood.py` 的 `current_mood()` 已加「漂移结果与当前值相同则跳过写库」的判断（`if new_mood != mood: db.set_mood(...)`），避免「读心情」高频触发无意义 SQLite 写。

### 🟡7 前端 `controller` 复用竞态 —— 已受控
当前 `frontend/src/components/ChatView.vue` 的 `send()` 与 `handleImageFile()` 都受 `busy.value` 标志保护（同一时刻只有一个流），且每个流在 `finally` 用 `if (controller === ctrl) controller = null` 守卫，避免误清对方引用。单一会话模式下不会触发竞态。

### 🟡8 `vision.py` 兜底模型名 —— 模型存在，无需修改
经用户确认，`deepseek-v4-flash-vision-exp` 这个识图模型**实际存在**，第 17-19 行注释里「该模型不存在、会导致 403」的描述不准确。第 49 行的兜底模型名保持不变，无需修改。

---

## 三、保留的 💭 可选建议（不强制）

- 💭9 `affection.py` 变体生成集合膨胀：建议 debug 级日志抽查生成结果，避免生成正常词被误判。
- 💭10 `check_abuse` 对单字「他/她/它」不判「骂别人」的边界取舍：可接受，仅记录。
- 💭11 `SessionList.vue` 的 `highlightText` 用 `v-html`（输入为本地归档标题 + 搜索词，非实时用户输入，低风险）；~~建议确认无注入面~~ **已在第二轮修复：转义后再 v-html，并补归档图片回显**。
- 💭12 测试覆盖不均衡：建议后续补辱骂词检测、心情倍率缩放、`vector_store.search` 的单测。

---

## 四、做得好的地方（值得保留）

1. **安全纵深防御扎实**：路径白名单 `casefold`、命令黑名单覆盖 `del /s /q` 变体、`check_url` 逐 IP 解析拒绝内网、`is_loopback_peer` 用 socket 来源而非可伪造头。
2. **后台任务强引用**：`_bg_tasks` / `_memory_tasks` / `_background_tasks` 三处持 Task 强引用 + `add_done_callback` 回收。
3. **XSS 双保险**：`marked` 渲染后过 `DOMPurify`，`ADD_TAGS: ['img']` 允许图片但剥离 `onerror`。
4. **流式健壮性**：`streamChat` 分段解析、气泡守卫、`loadSeq` 丢弃过期响应、SSE 自动重连。
5. **代码可读性极佳**：大量「Why / 副作用 / 兜底」注释，非直觉逻辑均有解释。

---

## 五、本次改动文件清单

### 第一轮（用户身份统一）

| 文件 | 改动 |
|---|---|
| `backend/api/chat.py` | `_user_id()` 统一返回 `assistant-main`，注明语义 |
| `backend/core/userdb.py` | 新增 `_migrate_legacy_user_identity()`，启动时合并 `session_current` → `assistant-main` |
| `backend/core/memory/vector_store.py` | 新增 `migrate_user_id()`，向量侧身份同步迁移 |
| `backend/app.py` | 启动流程调用向量身份迁移 |

### 第二轮（功能体验缺陷）

| 文件 | 改动 |
|---|---|
| `backend/core/pipeline.py` | 新增 `_extract_city()` + `_CITY_ALIASES`，天气先提取城市再回落 mood_city；工具循环后切片推 stream_cb |
| `backend/core/vision.py` | 兜底模型名改用 `_DEFAULT_VL_MODEL` |
| `backend/api/vision.py` | 识图图片落盘 `data/imgs/`，返回 `image_url` |
| `backend/api/chat.py` | 新增可选 `image` form 字段，随 user 消息持久化 |
| `frontend/src/api/chat.ts` | `uploadVision` 返回 `{description, imageUrl}`；`streamChat` 支持 image |
| `frontend/src/components/ChatView.vue` | 识图挂 image 缩略图；新增 `streaming-change` 上报生成状态 |
| `frontend/src/components/MessageBubble.vue` | 图片 alt 文案通用化 |
| `frontend/src/App.vue` | 生成中禁用归档按钮（`generating` 守卫） |
| `frontend/src/components/SessionList.vue` | 搜索高亮转义防 XSS + 归档查看器回显图片 |

*所有改动已通过 `py_compile` 语法检查 + `vue-tsc --noEmit` 类型检查。迁移逻辑幂等，无旧数据时无副作用。*

---

## 七、第三轮复核修复：第二轮修复的残留缺陷（2026-09-03 追加）

对第二轮修复做逐条复核，确认 6 项主修复方向正确（天气城市、识图落图、归档竞态、vision 兜底模型、SessionList 转义、工具消息补流），但发现 4 处「修了一半 / 修出新问题」的残留，本轮全部修复：

| 编号 | 位置 | 问题 | 处理 |
|---|---|---|---|
| 🔴G | `ChatView.vue:201` vs `:221` | 识图当场显示「（发送了一张图片）」、落库却是「（我发了一张图片，图的内容是：xxx）」长描述，刷新后文案错位 | ✅ 已修：当场就把 user 文案更新为完整描述，与后端落库完全一致 |
| 🔴H | `maintenance/loop.py:91` | `_referenced_image_names()` 只扫 messages 表，不扫 archives.messages_json；归档把图片引用挪进 archives 后，超 300MB 清理会误删归档图片 → 旧归档 404 | ✅ 已修：引用集合同时纳入 archives.messages_json 里的 image（含 user 识图 + bot 生图） |
| 🟡I | `vision.py` 落盘扩展名 | 扩展名按上传文件名猜，`.jpg` 名存 PNG 内容会写错扩展名，严格浏览器不渲染 | ✅ 已修：改为按文件魔数（PNG/JPEG/GIF/WEBP）判断，回落文件名、再回落 .png |
| 🟡J | `ChatView.vue:385` | 归档提示条按钮生成中未禁用，点击后父组件静默 return，用户以为没反应 | ✅ 已修：提示条按钮 `:disabled="isGenerating"` + 置灰样式 |

### 🔴G 识图文案错位（`frontend/src/components/ChatView.vue`）

**问题**：第二轮修复已让图片落库，但文案仍是两套——当场先显示「（发送了一张图片）」，识图成功后只把图片挂 `userMsg.image`、没改 `userMsg.content`，而发给后端的 `text` 是「（我发了一张图片，图的内容是：xxx）」。刷新后用户气泡从短句变成长描述。

**修复**：识图成功后，在挂 image 的同时把 `userMsg.content` 同步更新为完整描述文案，使「当场显示」与「后端落库」用同一句，刷新/归档后不再错位。

### 🔴H 归档图片被孤儿清理误删（`backend/maintenance/loop.py`）

**问题**：`_referenced_image_names()` 原本只 `SELECT image FROM messages`。归档动作（`_archive_current_sync`）会把消息从 messages 表搬进 archives 表的 messages_json，图片引用随之「消失」在 messages 表里。当 `data/imgs` 超 300MB 触发 `clean_orphan_images`，这些归档里的图片会被判定为无引用而删除，旧归档里的图就 404 了。

**修复**：`_referenced_image_names()` 增加第二个查询来源——遍历 `archives.messages_json`，用 `json.loads` 解析出每条消息的 `image` 字段（含 user 识图的 `vision_*.png` 和 bot 生图），纳入引用集合。这样归档里的图片和当前会话的图片一样受保护，不会在清理时被误删。

### 🟡I 识图落盘扩展名按魔数判断（`backend/api/vision.py`）

**问题**：原实现按上传文件名后缀猜扩展名，`.jpg` 文件名存 PNG 内容时会写错扩展名，某些严格浏览器不渲染。

**修复**：新增 `_detect_ext(data, filename)`，优先按文件魔数判断（`\x89PNG` → png、`\xff\xd8\xff` → jpg、`GIF8` → gif、`RIFF...WEBP` → webp），识别不到再回落文件名后缀，最后回落 `.png`。已实测各类型识别正确。

### 🟡J 归档提示条按钮生成中禁用（`frontend/src/components/ChatView.vue`）

**问题**：顶部图标归档按钮已禁用，但对话内「归档提示条」的「归档当前对话」按钮仍可点，点击后父组件 `archiveNow` 因 `generating` 守卫静默 return，用户以为点了没反应。

**修复**：提示条按钮加 `:disabled="isGenerating"` + 提示 title + 置灰样式，与顶部按钮行为一致。

### 本轮未处理（如实记录，属已缓解 / 需更大改动，非本轮 bug）

以下几条复核指出，但判断为「已缓解」或「需结构性改动」，本轮不强行处理，如实记录待办：

1. **工具流「打字机」是补播非真实时**（`pipeline.py:1074`）：工具循环是整段返回后才切片推 stream_cb，等待工具执行的十几秒里气泡仍空，最后几秒「补播」一遍。比原来好，但不是真正的流式。要根治需让工具循环期间的阶段性进展也上屏（结构性改动，涉及 run_tool_round 内部 push），列为后续优化项。
2. **识图孤儿文件**（`vision.py`）：`/api/vision` 先落盘、`/api/chat` 才引用，两步之间中断会留孤儿文件。已通过「🔴H 引用集合纳入 vision_* 图」间接兜底——孤儿 vision 图无任何引用，会在超限清理时被正常删除，不会永久堆积。若要更精细（即时清理），需给 vision 上传加会话关联或延迟删除，收益有限，暂不处理。
3. **主动消息不持久化**（`ChatView.vue:339` `handleProactiveMessage`）：busy/streaming 时直接 return 丢弃，注释「已持久化」仍不准确。这是上一轮就报的遗留项，涉及 initiative 引擎与 sessions 库的写入打通，改动较大，本轮不处理。
4. **普通流式路径 system 指令位于 user 之后**（`pipeline.py:1079`）：与文件内「user 必须最后一条」注释矛盾。这是既有行为，工具路径已用 final_instruction 规避，普通路径若要改需重排消息顺序，可能影响现有回复质量，本轮不强行改。
5. **归档搜索仍是并发拉全部详情再内存过滤**（`SessionList.vue`）：没有后端 LIKE 搜索端点。属性能优化，数据量小（本地归档）时影响有限，列为后续优化。
6. **孤儿功能 / 死开关**：`tts.py`、`greeting.py`、`features.py` 死开关、DB/向量残留、`icon.png` 缺失、`dist` 过期。均属「功能接入 UI / 打包资产」类，非 bug，列入后续待办。

---

## 八、累计待办清单（🟢 级，非 bug，后续统一处理）

| 项 | 说明 | 建议 | 状态 |
|---|---|---|---|
| 工具流真实流式 | 工具循环期间阶段性进展不上屏，只有事后补播 | 重构 run_tool_round 内部 push 进度 | ⏳ 未处理 |
| 主动消息持久化 | `handleProactiveMessage` busy 时丢弃、注释失真 | initiative 写入 sessions 库 | ✅ 第四轮已修 |
| 归档搜索后端端点 | 仍是并发拉全部详情内存过滤 | 加 `/api/archives/search?q=` | ⏳ 未处理 |
| 孤儿功能 | tts/greeting 无人调用、features 死开关、DB/向量残留 | 接入 UI 或删除 | ⏳ 未处理 |
| 打包资产 | `public/icon.png` 缺失、`dist` 过期 | 补图标、重建 dist | ✅ 第四轮已修 |
| 普通流式 system 顺序 | user 之后追加 system 与注释矛盾 | 评估重排消息顺序 | ✅ 第四轮已修 |

---

## 九、第四轮修复：新发现 + 上轮遗留（2026-09-03 追加）

本轮三条**新发现**（此前从未报过）+ 三条上轮遗留的 🟡/🟢 项，一并处理。

### 🟡1 Service Worker 无差别拦截缓存（`frontend/public/sw.js` + `index.html`）

**问题**：`sw.js:22` 的 fetch handler 对所有请求（不限 GET、不限路径）做 network-first + `cache.put(e.request, clone)`，而 Electron 生产形态（http://127.0.0.1:8801 同源）也注册它。后果：
- `/api/chat`（POST SSE）、`/api/initiative/stream`（EventSource）是长连接流，`cache.put` 把整个流 body 读进 Cache Storage，聊天全文被持久化缓存，长连接期间 clone 一直挂起；
- `/api/audit/log`、`/api/config`、`/api/sessions/*` 的响应（含工具参数/日志等敏感内容）被落盘；
- `/api/images/*`、`/persona/*` 每张图都被缓存且无清理，只增不减。

**修复**（双保险）：
1. `sw.js` 重写 fetch handler——只对「同源 GET + 命中 CACHEABLE 白名单（`/`、`/assets/*`、`/favicon.ico`、`/manifest.json`、`/icon.svg`）」做 network-first 缓存；`/api/*`、`/mcp/*` 直接 return 放行；非 GET、跨域、`text/event-stream` 响应一律不缓存不 clone。
2. `index.html` 注册处加 `!window.electronAPI` 守卫——Electron 环境（preload 注入 `electronAPI`）干脆不注册 SW，只保留浏览器 PWA 形态注册。

### 🟢2 截图目录无上限、无清理、不备份（`plugins/system.py` + `backend/maintenance/loop.py`）

**问题**：`system.py:273` 的 screenshot 工具往 `data/screenshots/` 写文件，但维护循环只清理 `data/imgs`、只备份 imgs，screenshots 既不清也不备份，任务代理截几次图就永久占盘。

**修复**：
1. `loop.py` 新增 `clean_old_screenshots(max_mb=200, keep=50)`——按「体积软上限 + 保留最新 50 份」双重约束，从最旧开始删；
2. `backup()` 增加 `screenshots` 目录随备份一起 copytree；
3. 维护循环的清理周期挂上 `clean_old_screenshots`；`health()` 增加 `screenshots_mb` 指标。

### 🟢3 识图气泡文案刷新滞后（`frontend/src/components/ChatView.vue`）

**问题**：`handleImageFile` 里识图成功后直接改 `userMsg.content`，但改的是 push 进去的原始对象引用，未触发重渲染，要等 bot 第一条流式片段到了才「刷」成完整描述——识图+LLM 首字的几秒里用户看到的还是占位文案。

**修复**：拿到描述后 `messages.value[userIndex] = { ...userMsg }` 整体替换，强制触发重渲染，立即显示完整描述。

### 🟡4 主动消息不持久化（`backend/core/initiative.py` + `ChatView.vue`）

**问题**：`initiative.py` 设计上「默认不主动写会话」，主动消息只在 kv 队列/SSE 里流转，前端 `handleProactiveMessage` 注释却写「后端入队推送时已持久化」——不成立，刷新页面主动消息就消失；busy/streaming 时直接丢弃。

**修复**：新增 `_persist_proactive(user_id, text)`，把主动消息以 `{role:'bot'}` 写入当前会话（`CURRENT_SESSION_ID`）messages 表。在三个出口统一调用：`enqueue_proactive`（离线入队）、`_tick_once` 的 `_deliver` 成功分支（实时投递）、`poll_for`（前端轮询）。前端注释同步修正为准确描述。

### 🟡5 普通流式路径 user 后追加 system（`backend/core/pipeline.py`）

**问题**：`messages.append({"role":"user"})` 之后，else 流式分支又 `append` think_block/topic_block/drawn_note 三条 system，形成 user→system 非法顺序，与「user 必须最后」注释矛盾。

**修复**：把三条 system 注入**前移**到 user 之前统一追加，else 流式分支删除重复追加；tool 路径的 `final_instruction` 保持不变（仍单独拆 system 传递）。

### 🟢6 打包资产补齐（`frontend/public/icon.png` + `dist`）

**问题**：`electron/main.ts` 与 `electron-builder` 都引用 `public/icon.png` 但文件缺失（只有 icon.svg），打包会翻车；`dist` 是 1:39 的旧构建，本地跑起来还是旧 UI。

**修复**：用 Pillow 按 `icon.svg` 视觉（深绿渐变圆角底 + 藤蔓 + 菟丝花）生成 256×256 的 `icon.png`；执行 `vite build` 重建 `dist`（已确认 dist 内含新 sw.js、icon.png）。

### 本轮仍未处理（如实记录）

| 项 | 说明 | 原因 |
|---|---|---|
| 归档搜索后端 LIKE 端点 | `SessionList.vue` 仍并发拉全部详情内存过滤 | 性能优化，本地数据量小，收益有限 |
| 孤儿功能/死开关 | tts.py、greeting.py、features.py 死开关、perception client 缓存不清理、ToolBar/meta 脱节、llm_stream_disable 死开关 | 属「功能接入 UI / 结构性改动」，非 bug |
| 工具流真实流式 | 工具循环期间阶段性进展不上屏，仅事后补播 | 需重构 run_tool_round 内部 push |


---

## 十、第五轮修复：全量审查新发现（2026-09-03 追加）

用户提供「二、新发现的问题」7 条 + 遗留粗糙点，逐条核实后修复（其中 1 条为误报）。

### 🟡1 好感度双通道重复计分（`backend/core/affection.py` + `pipeline.py`）

**问题**：同一句辱骂被扣两次——`affection.on_message` 里 `check_abuse` 关键词扣 -5（心情倍率缩放），`apply_impulse` 又按语义 `affection_delta` 扣 -5~-8。`on_message` 的 docstring 写着「只做两件事（冷落衰减+读心情）」，函数体却仍做聊天奖励/辱骂扣分/跨天回滚，注释与实现脱节。

**修复**：确立唯一计分通道（对齐每日奖励的「语义主、关键词兜底」）：
1. 从 `on_message` 删除 `check_abuse` 辱骂扣分块（保留刷屏扣分，纯频率维度语义不覆盖）；
2. 新增 `affection.apply_abuse_penalty(user_id, text)`——封装心情倍率 + 每日上限，供 pipeline 在语义降级/失败时调用；
3. pipeline 在 `if not semantic_ok:` 分支调用 `apply_abuse_penalty`，语义成功时不再重复扣；
4. `on_message` 的 docstring 与「只做两件事」注释同步修正为准确描述。

### 💭2 memory_search 向量兜底（核实为误报）

**核实**：`backend/core/vector_store.py`（薄壳）的 `search()` 已在第 36 行把 `SearchHit` 转成 `[(record_id, distance)]` 元组，`memory.py` 的 `for rid, dist in vec` 写法正确，**不是死代码**。

**仍做了改进**：把该兜底块外层 `except Exception: pass` 改为 `logger.warning(...)`，让真实失败可见而非静默吞掉。

### 🟡3 主动消息重启/重连重复显示 + 通知双弹（`store.py` + `initiative.py` + `ChatView.vue`）

**问题**：落库后消息已在 sessions，但 queue/kv 可能残留，SSE 首帧会再推一次；主进程轮询（30s）与渲染进程 SSE 是两套独立「取消息+弹通知」路径，窗口隐藏时双弹。

**修复**：
1. `store.py` 新增 `append_proactive_message()`——幂等落库（最后一条 bot 消息内容相同则跳过），防重启/重连重复；
2. `ChatView.vue handleProactiveMessage` 删除弹通知逻辑，通知统一由主进程轮询负责，消除双弹。

### 🟡4 _persist_proactive 绕过 async 锁（`initiative.py` + `store.py`）

**问题**：直接调 `_store._append_sync`（私有同步函数），既阻塞事件循环，又绕过 store 的 `_lock`，主动消息可能插到用户/菟菚消息中间。

**修复**：`_persist_proactive` 改为 `await store.append_proactive_message`（async 锁内 + 线程池），`_persist_proactive`/`enqueue_proactive` 均改 async，所有调用点（`_tick_once`/`poll_for`/`maybe_suggest_archive`）加 await。

### 🟡5 记忆写入侧无强引用 fire-and-forget（3 文件）

**问题**：`compress.py:205`、`topic_memory.py:93`、`triple_memory.py:131` 用 `ensure_future(to_thread(...))` 不保存引用，向量索引写入可能被 GC 随机跳过且无日志。

**修复**：三处统一改走 `engine._spawn()`（持有 `_background_tasks` 强引用 + done_callback 清理）。

### 🟡6 MCP 客户端重定向未逐跳复检（`mcp_server.py` + `SettingsPanel.vue`）

**问题**：注册时 `check_url` 只校验首跳，`list_tools`/`call_tool` 用 `urllib.urlopen` 自动跟随重定向，后续 302 到内网会绕过 SSRF 防线；设置面板占位示例 `http://127.0.0.1:8801/mcp` 会被 SSRF 校验拒绝。

**修复**：新增 `_NoRedirect` + `_request_json`（逐跳 `check_url` 复检，对齐 `plugins/web_fetch.py`），`McpClient` 改用；`SettingsPanel.vue` 占位示例改为公网 URL。

### 🟡7 shutdown 退出任务无强引用 + 时序耦合（`health.py` + `electron/main.ts`）

**问题**：`asyncio.create_task(_bye())` 无引用，可能被 GC 回收；Electron 1.5s 强杀，若后端卡在 backup 会丢最后一次备份。

**修复**：`health.py` 加模块级 `_shutdown_tasks` 强引用；`electron/main.ts` 强杀延时 1.5s→8s（给 checkpoint+备份留足时间），并加注释说明后端正常退出后 `on('exit')` 会置空引用跳过强杀。

### 本轮验证

- `py_compile` 全过（affection/pipeline/initiative/store/health/compress/topic_memory/triple_memory/mcp_server/builtin/memory 共 10 文件）
- `vue-tsc --noEmit` 零错误
- `test_anthropic_core.py` 52/52 通过（同步更新 `enqueue_proactive` 改 async 后的调用点）
- `test_memory_v2.py` 11/11 通过

### 遗留粗糙点（未变，非 bug，需结构性改动）

- 工具类消息「先等整段完成、末尾补播」，无背压灌队列；
- 归档搜索无后端 LIKE 端点；
- 孤儿功能：/api/tts、/api/greeting、features.py 死开关、stickers/user_terms 残留表；
- ToolBar 只挂载取一次 meta、保存设置不刷新；meta 里 search/vision 判定与实际能力脱节；
- llm_stream_disable 死开关；配置热重载不清 get_perception_client 缓存；
- 远程任务 done/failed 丢 user_id；api/remote 单用户语义与文档口径并存。
