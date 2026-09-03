# 代码审查报告 V11（第三轮深入 + 主动归档按钮）

> 审查范围：意图路由 / 向量库 / MCP 服务器 / 心情规则 / 行为映射 / LLM 感知 / 拟人状态机
> 审查人：代码审查专家（CodeReviewExpert）
> 日期：2026-09-03

---

## 一、总体结论

本轮覆盖了前两轮没碰到的**核心拟人层**（状态机、感知、行为映射、记忆、向量检索）和**MCP 服务器**。

**P0=0、P1=1、P2=2、P3=1**。

项目整体质量依然扎实，尤其是**拟人层设计**——状态机的衰减模型（短时半衰期 3h / 长期 30 天）、感知层的 LLM+关键词双轨降级、行为帧的自然语言注入，都是深思熟虑且注释到位的实现。安全设计（SSRF 逐跳复检、维度一致性检查、MCP 来源 IP 鉴权）继续保持高水准。

本轮唯一一个 P1 是**向量检索静默失效**，已修复。

---

## 二、问题清单

### 🔴 P1-1：向量检索 zip 对齐导致结果静默丢失/抛异常（已修复）

**位置**：`backend/core/memory/vector_store.py` 第 179 行 `search()`

**现象**：用 `list(zip(ids_raw[0], dists_raw[0], docs_raw[0], metas_raw[0]))` 对齐 Chroma 返回的四个列表。

**Why（两个真实缺陷）**：
1. Chroma 的 `distances` 在 cosine 空间下**可能不返回**（为空列表）。此时 `zip(ids, [], ...)` 会按最短序列截断成 **0 个元素**，3 条本该命中的检索结果被静默丢弃，向量检索返回空。
2. 当 `metadatas` 未配置时，Chroma 返回的 `metas_raw[0]` 是 `None`，`zip` 直接抛 `TypeError`，被外层 `except` 吞掉——**整次 search 静默返回 `[]`**，且无任何日志。

**影响**：菟菚的「翻旧账/回忆」能力（依赖向量检索）在特定 Chroma 配置下会静默失效，用户感知为「她什么都记不住」。

**修复**：改为显式按索引对齐，缺字段补 `None` 占位，不再用 zip 隐式截断。已验证三种场景（距离缺失/元数据为 None/正常）均正确返回。

---

### 🟡 P2-1：`_get_archive_sync` / `get_archive` 的归档 title 兜底

**位置**：`backend/session/store.py` `_archive_current_sync`

**说明**：归档标题取「第一条用户消息前 20 字」，若第一条消息是图片（`content` 为空），会正确跳过。逻辑无 bug，但**归档列表里的 `title` 默认值是 `"归档"`**——当用户第一条消息就是图片时会显示「归档」，标题不友好。

**建议**（非必须）：图片消息时标题可兜底为「图片对话」或取时间。

---

### 🟡 P2-2：`mcp_server.py` 的 `McpClient.call_tool` 超时 30s 固定

**位置**：`backend/tools/mcp_server.py` 第 230 行

**说明**：外部 MCP 工具调用 `urlopen(timeout=30)` 写死 30 秒。对慢工具（如生成类）可能过早超时，且失败时只是静默 `return False`（`register_external_server` 里 catch 后 return False），没有把具体错误透出给用户。

**建议**：超时做成可配置，失败时记录具体异常（`logger.warning` 已部分覆盖，但 `list_tools` 的失败仅 `return False` 无日志）。

---

### 💭 P3-1：`state.py` 的 `_memory_key` 参数冗余

**位置**：`backend/core/state.py` 第 95-96 行

**说明**：`_memory_key(user_id)` 返回固定字符串 `"state:emotion_memory"`，参数 `user_id` 实际没用到（情绪记忆按 `kv_get(user_id, key)` 已经是用户维度隔离的）。同理 `_archive_key` / `_event_key`。

**建议**：签名可去掉 `user_id` 参数，避免误导；或注释说明「key 不含 user_id 是因为 kv_store 已按 user 隔离」。属风格/可读性 nits，不影响正确性。

---

## 三、本轮正面亮点（值得表扬）

1. **拟人状态机的衰减模型**（`state.py`）：短时情绪半衰期 3h、长期情绪档案半衰期 30 天、事件级记忆半衰期 30 天，三档衰减让「她记得一阵、但不会永久记仇」这个拟人细节落地得很自然。
2. **感知层双轨降级**（`perception.py`）：LLM 语义感知失败时无缝回退到关键词规则，且降级结果带 `degraded` 标志供 pipeline 判断，绝不阻塞对话。
3. **行为帧自然语言注入**（`behavior.py`）：把冰冷的状态数值翻译成 LLM 能照着演的自然语言指令，且「不翻旧账、不点名」的克制写得很到位。
4. **规则中心化**（`mood_rules.py`）：所有可调参数外置到 `mood_rules.json`，带 mtime 短缓存热更新，冒犯词用多字词避免误伤。

---

## 四、主动归档按钮（本轮新增功能）

**需求**：给菟菚添加一个「主动归档对话」的按钮，用户可随时把当前对话打包存入归档库并清空，而非等会话自动结束。

**实现**（纯前端，后端 `POST /api/sessions/archive` 早已就绪）：

| 文件 | 改动 |
|------|------|
| `frontend/src/App.vue` | ① 导入 `archiveCurrent`；② 新增 `archiveNow()` 函数（归档成功后自增 `sessionListKey` + `chatReloadKey`）；③ 在 header-right 加一个归档图标按钮（带 `archiving` 防重复点击 + 空会话提示） |
| `frontend/src/components/ChatView.vue` | ① 新增 `reloadKey` prop；② `watch(reloadKey)` 触发时清空消息并重载当前会话 |

**交互逻辑**：
- 点击归档按钮 → 调 `archiveCurrent()` → 后端打包当前消息存入 `archives` 表并清空 `messages`
- 成功后：侧栏归档列表刷新（`sessionListKey`）+ 对话区清空重载（`chatReloadKey`）
- 当前会话为空时弹窗提示「当前会话还没有可归档的内容」
- 已通过 `vue-tsc --noEmit` 类型检查，零错误

---

## 五、累计 backlog（跨轮汇总，未动，等用户决定）

| 编号 | 级别 | 位置 | 问题 |
|------|------|------|------|
| B1 | 🟡 | `backend/core/userdb.py` | `update_task` 用 f-string 拼 SQL（当前安全，字段名来自白名单，属坏味道） |
| B2 | 🟡 | `plugins/code_exec.py` | 子进程超时 kill 未清孙进程，建议 `taskkill /T` |
| B3 | 💭 | `backend/api/agent.py` | `_drop_channel_later` 延迟清理已修，但可再确认 channel 引用链 |
| B4 | 💭 | `backend/core/pipeline.py` | 单字「查」可能误触发工具循环 |
| B5 | 💭 | 多处 | `D:\DSH` 硬编码路径（`config.py` 等） |

---

## 六、下一步建议

1. 建议优先处理 B1（f-string SQL 消除坏味道）和 B2（孙进程清理），都是低风险可快速修复。
2. 前端「主动归档」按钮已完成，建议实际跑起来点一下验证交互（归档 → 清空 → 侧栏出现新归档）。
3. 长期看，可给 `vector_store.search()` 补一个单元测试，覆盖「distances 缺失」和「metadatas 为 None」两个边界，防止回归。
