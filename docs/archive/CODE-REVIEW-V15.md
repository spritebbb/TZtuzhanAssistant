
---

## 十一、第六轮修复：遗留粗糙点收尾（2026-09-03 追加）

上轮遗留清单里「非 bug、但影响日常体验或属半成品」的项，本轮处理其中可低成本见效的部分。

### 🟡1 llm_stream_disable 死开关复活（`config.py`）

**问题**：`llm.py:236` 读 `config.llm_stream_disable`，但 `config.py` 从未定义该字段，`getattr` 永远返回 False——流式逃生开关形同虚设。

**修复**：`config.py` 补 `self.llm_stream_disable = os.getenv("LLM_STREAM_DISABLE", "0") != "0"`，开关真正可用（设 `LLM_STREAM_DISABLE=1` 退回整句返回）。

### 🟡2 配置热重载不清 perception client 缓存（`config_api.py`）

**问题**：`config.reload()` 后只清 `llm._client`，感知层独立 client（缓存于 `get_perception_client._client`）不清理，改 `LLM_PERCEPTION_*` 端点/模型后仍走旧配置。

**修复**：`config_api.py` 重载缓存重置处增加 `_llm.get_perception_client._client = None`。

### 🟡3 远程任务 done/failed 丢 user_id（`api/remote.py`）

**问题**：`_run` 里 done/failed 状态字典缺 `user_id`，与 running 状态不一致（running 有 user_id，终态没有）。

**修复**：done/failed 两个终态字典补 `"user_id": uid`，与 running 对齐。

### 🟢4 features.py 死开关清理（`features.py`）

**问题**：`terms_enabled`/`style_enabled`/`emotion_sticker_enabled` 三个开关全项目无消费方（对应口头禅/表达风格/表情包功能已删除，与 stickers/user_terms 残留表同源）。

**修复**：从 `FLAG_DEFAULTS` 移除三个死开关，仅保留唯一活跃的 `profile_enabled`；`set_flag`/`all_flags` 补注释说明「无 UI 入口、保留作未来接入」。

### 🟡5 归档搜索后端 LIKE 端点（`store.py` + `sessions.py` + `SessionList.vue` + `sessions.ts`）

**问题**：前端搜索归档是「拉全量列表 + 逐个拉详情」N+1 模式，标题命中也要拉一次完整详情。

**修复**：
1. `store.py` 新增 `_search_archives_sync(q)`——用 `LIKE ... COLLATE NOCASE` 匹配 title + messages_json 文本，一次查询返回命中归档的完整详情（去重 + created_at 降序）；新增 async 包装 `search_archives(q)`；
2. `sessions.py` 加 `GET /api/sessions/archives/search?q=`（声明在 `/archives/{archive_id}` 之前，避免被动态路由吞掉）；
3. `sessions.ts` 加 `searchArchives(q)`；`SessionList.vue` 改用新端点，删掉 N+1 逻辑。

### 本轮仍未处理（如实记录，属结构性改动/需用户拍板）

| 项 | 说明 | 原因 |
|---|---|---|
| 工具流真实流式 | 工具循环期间阶段性进展不上屏，仅事后补播 | 需重构 run_tool_round 内部 push 进度 |
| 孤儿功能 /api/tts /api/greeting | 后端能力已就绪、前端无入口 | tts 的缓存清理（clean_cache_async）被维护循环活跃使用，删不得；greeting 完整实现可独立用。接入 UI 还是删除需用户拍板 |
| ToolBar 保存设置不刷新 | 仅挂载时取一次 /api/meta | 需跨组件事件/状态共享，非纯 bug |
| meta 判定与实际能力脱节 | search 判 `search_api_key`、vision 判 key，但无 key 也能用 bing/ddg、只配 LLM key 也有识图兜底 | 判定口径需重新定义 |
| 远程任务单用户语义 | api/remote 单用户隔离与「assistant-main」文档口径并存 | 需注释说明，非 bug |

### 本轮验证

- `py_compile` 全过（config/config_api/remote/features/store/sessions 共 6 文件）
- `vue-tsc --noEmit` 零错误
- `test_anthropic_core.py` 52/52 通过（无回归）

---

## 十二、第七轮修复：两个小项 + 工作流真实流式（2026-09-03 追加）

上轮遗留清单里最后的几项，本轮收尾：两个低成本小项 + 一直挂账的「工具流真实流式」核心重构。

### 🟡1 ToolBar 保存设置不刷新（`SettingsPanel.vue` + `ToolBar.vue`）

**问题**：`ToolBar.loadTools()` 仅在 `onMounted` 拉一次 `/api/meta`，设置面板改完联网/天气/生图/识图等开关后，工具栏的「联网/识图」状态不刷新。

**修复**：`SettingsPanel.save()` 成功后 `window.dispatchEvent(new CustomEvent('tztuzhan:config-saved'))`；`ToolBar` 挂载时监听该事件 → 重新 `loadTools()`，卸载时移除监听。用轻量自定义事件做跨组件通知，不引入全局 store。

### 🟡2 greeting 前端无入口（`ChatView.vue`）

**问题**：后端 `/api/greeting`（久别问候）已完整实现并落库，但前端从未调用，功能等于不存在。

**修复**：`ChatView` 新增 `checkGreeting(sessionId)`，`onMounted` 时调 `/api/greeting?session_id=`，返回问候语则作为 bot 消息即时追加展示（后端已持久化，刷新后仍在）。问候失败静默忽略，不影响主流程。

### 🟡3 工具流真实流式（端到端重构，核心）

**问题**：工具循环期间（模型决定调工具 → 执行 → 等结果 → 再生成），所有阶段性进展都不上屏，前端气泡空窗到 `done` 帧才整段「哐」出来。之前只是在拿到最终文本后切片补推一次 `stream_cb` 做打字机效果，但工具执行的真实过程用户完全看不到。

**修复**：引入 `on_progress` 回调，贯穿「工具循环 → pipeline → SSE → 前端气泡」全链路：

| 层 | 文件 | 改动 |
|---|---|---|
| 工具循环 | `tools/tool_loop.py` | `run_tool_loop`/`_run_native`/`_run_text` 加 `on_progress`；在「每轮思考前」「调工具前」「工具完成后」「最终回复前」推 `{"type":"thinking"/"tool"/"tool_done","name":...}`；`_progress` 包装吞异常 |
| 服务层 | `tools/service.py` | `run_tool_round` 加 `on_progress` 透传 |
| 管线 | `core/pipeline.py` | `process`/`_process_locked` 加 `progress_cb`，传给 `run_tool_round(on_progress=progress_cb)` |
| SSE | `api/chat.py` | `_progress_cb` 事件入队（`__tool__` 标记），sse 生成器 `yield {"tool": event}` |
| 前端 API | `api/chat.ts` | `ChatCallbacks` 加 `onTool`，解析 `obj.tool`；新增 `ToolProgressEvent` |
| 气泡 | `ChatView.vue` + `MessageBubble.vue` | `toolStatus` ref + `toolLabels` 中文映射 + `handleToolEvent`；两个 `streamChat` 都接 `onTool`；content 为空且流式中时，藤蔓光标旁显示脉冲圆点 + 进度文案（"正在联网搜索…"等） |

**兼容性**：`agent/session.py`、`api/remote.py` 两处 `run_tool_round` 调用方均为关键字调用、不传 `on_progress`（默认 `None`），不破坏。

**测试同步**：`test_p4_agent.py` 的 `fake_loop`（monkeypatch 覆盖 `run_tool_loop`）签名补 `on_progress=None`。

### 本轮验证

- `py_compile` 全过（tool_loop/service/pipeline/chat 共 4 文件）
- `vue-tsc --noEmit` 零错误
- `test_anthropic_core.py` 52/52 通过（无回归）
- `test_p4_agent.py` 3/3 通过
- `vite build` 成功（dist 已重建）

### 🟢4 修复 test_p6_smoke 既有测试 bug（顺手）

**问题**：`test_p6_smoke.py` 第 5 项断言 `GET /api/sessions`（裸路径）== 200，但 `sessions.py` 从未定义裸路径路由（仅 `/{session_id}` 动态路由），前端也从不请求裸路径（只用 `/api/sessions/current` 等），故 404 是既有的测试 bug、对生产零影响。

**修复**：断言改为测真实存在的 `GET /api/sessions/current`，smoke 测试 7/7 全过。

## 十三、第八轮修复：边界竞态 + 降级一致性 + 资源收尾（2026-09-03 追加）

用户列出 6 条，优先级 1 → 2 → 3，功能层已无明显粗糙大块，剩边界竞态与降级一致性。

### 🟡1 降级路径辱骂「双扣」（唯一称得上 bug 的残留）

**问题**：`perception._fallback_rule` 辱骂时返回 `affection_delta=-5` 且 `degraded=True`；pipeline 先 `apply_impulse(affection_delta=-5)` 扣一次，又因 `not semantic_ok` 走 `apply_abuse_penalty` 再扣 -5 → LLM 不可用/超时时一句辱骂「−5 + −5」。

**修复**（`perception.py`）：`_fallback_rule` 辱骂时 `affection_delta=0`（保留 `emotional_hit` 情绪冲击），好感度统一交关键词兜底 `apply_abuse_penalty` 处理。非辱骂正向信号（care/apology/sharing 等语义不覆盖的部分）仍保留 `affection_delta`。

### 🟡2 主动消息通知/去重两个竞态角

**问题**：通知统一交主进程后，窗口隐藏时 SSE 先消费消息 → 主进程 30s 轮询取空 → 完全无通知；且 `notifiedKey` 是组件内状态，重挂载重置导致同一条消息推成两条相邻气泡。

**修复**：
- 主进程 `notify` IPC handler 加共享去重 `lastNotifiedText`（与 `pollInitiative` 通道共享），谁先到只弹一次。
- 渲染进程 `handleProactiveMessage` 页面隐藏（`document.hidden`）时主动请求一次 `window.electronAPI.notify`，主进程去重兜底不双弹。
- 去重改为「与消息列表最后一条 bot content 相同则跳过」，与数据库端幂等逻辑一致。

### 🟡3 busy/streaming 时主动消息静默丢失

**问题**：`handleProactiveMessage` 生成中直接 return，消息已落库但界面不显示也不通知。

**修复**：生成中存 `pendingProactive`，两处流结束 `finally` 里 `flushPendingProactive()` 补插气泡。

### 🟡4 归档搜索返回面收窄

**问题**：`_search_archives_sync` 直接返回完整 `messages_json`，归档多/搜索词泛时一次拉回大量数据。

**修复**（`store.py`）：加 `_SEARCH_LIMIT=50` + `_SEARCH_QUERY_MAX=200` + `preview` 摘要字段（`_preview_for` 抽命中词附近 80 字符）；前端 `sessions.ts` 加 `ArchiveSearchResult` 类型，`SessionList` 点进再 `getArchive` 拉完整详情，模板展示 preview。

### 🟡5 数据层「部分加锁」一致性

**问题**：`UserDB` 只用一把 RLock 装饰了部分方法，`get_user`/`get_mood`/`messages_after`/`add_fact`（写）/`save_triples` 等大量直读直写碰共享 `self.conn`。

**修复**（`userdb.py` + `triple_memory.py`）：所有直接碰 `self.conn` 的未加锁方法统一补 `@_locked`（含写方法 `add_fact`）；模块级函数用 `with db._lock:` 包裹；`triple_memory` 的 `save_triples`/`query_triples`/`_idx` 后台读同样加锁。

### 🟢6 低优先级打磨

- **ToolBar 状态刷新**：前轮已做；本轮补 `SessionList` 侧栏心情监听 `tztuzhan:config-saved` → `refreshMood()`。
- **MCP 占位示例**：前轮已改成 `https://example.com/mcp`（公网）。
- **识图 user 气泡重渲染**：前轮已做（整体替换消息对象强制刷新）。
- **测试编排**：新增 `tests/test_edge_regressions.py`，7 个 `suite_*` 单测沉淀本轮行为（降级双扣、preview 摘要、bad json、查询长度上限、主动消息幂等、路由顺序），pytest 直接收集。

### 本轮验证

- `py_compile` 全过（perception/store/sessions/userdb/triple_memory）
- `vue-tsc --noEmit` 零错误
- `pytest tests/test_edge_regressions.py` 7/7 通过
- `test_anthropic_core.py` 52/52、`test_p4_agent.py` 3/3、`test_p6_smoke.py` 7/7
- `vite build` 成功（dist 已重建）
