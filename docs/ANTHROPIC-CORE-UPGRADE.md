# 拟人核心层升级交付报告

> 目标：提升菟菚的好感度系统与拟人程度，接近正常人的行为模式。
> 方向：重写核心层，优先「行为动态层」，覆盖「聊天像真人 + 有主动性 + 情绪真实」三维目标。

---

## 一、核心结论

原有系统的短板不在「人设」（persona 卡已很细腻），而在**状态是死的、行为是演出来的**：

- 好感度靠几百个硬编码关键词打点，模型说一句没匹配到词的真情实感系统就无感；
- 菟菚每轮对话「失忆重来」，没有「此刻自己的状态」的连贯性；
- 心情/好感度是后台数字，缺一个把「状态 → 可感知行为」串起来的动态层。

本次重写引入了一个**多维状态机 + LLM 语义感知 + 行为映射 + 主动性引擎**的四层结构，让菟菚从「静态人设临时演」变成「持续演化的存在，行为由状态驱动」。

---

## 二、新增/修改的文件

### 新增（backend/core/）

| 文件 | 职责 | 说明 |
|---|---|---|
| `state.py` | 多维状态机核心 | 情绪/精力/好感度/关系阶段/情绪记忆，持续演化 + SQLite 持久化 |
| `perception.py` | LLM 语义感知 | 一次 LLM 调用读懂每句话，输出结构化 delta；失败降级关键词规则 |
| `behavior.py` | 行为映射层 | 状态 → 可感知行为（语气/主动性/反应），生成「行为帧」注入 prompt |
| `initiative.py` | 主动性引擎 | 久未聊 + 关系够近时主动开口，严格频率限制不骚扰 |

### 新增（backend/api/）

| 文件 | 职责 |
|---|---|
| `initiative.py` | `GET /api/initiative` 前端轮询端点，优先取待投递队列、再兜底即时生成 |

### 修改

| 文件 | 改动 |
|---|---|
| `backend/core/persona.py` | `build_system_prompt` 注入「行为帧」，取代单一心情数值 |
| `backend/core/pipeline.py` | 接入语义感知 + 状态演化，避免与旧关键词奖励双重计分 |
| `backend/core/userdb.py` | 新增 `kv_del`（供待投递队列出队） |
| `backend/core/initiative.py` | 新增待投递队列（`enqueue_proactive`/`dequeue_proactive`），后台生成消息先入队再投递 |
| `backend/app.py` | 注册 initiative 路由 + 启动后台主动性 loop（真·推模式） |
| `frontend/electron/main.ts` | 新增 `notify`/`focus-window` IPC，系统通知 + 点击聚焦窗口 |
| `frontend/electron/preload.ts` | 暴露 `notify`/`focusWindow` 给渲染进程 |
| `frontend/src/api/sessions.ts` | 新增 `getInitiative`（拉取主动消息） |
| `frontend/src/components/ChatView.vue` | 30s 轮询主动消息 → 追加气泡 + 窗口隐藏时弹系统通知 |
| `frontend/src/env.d.ts` | 补充 electronAPI 类型声明 |

### 新增测试

| 文件 | 结果 |
|---|---|
| `tests/test_anthropic_core.py` | 29 项全通过（含待投递队列 + 双轨合并回归用例） |

---

## 二.5、双轨合并（v3：理顺好感度/心情的唯一状态源）

> 上一版引入了「语义感知」但没删旧的关键词打点，导致好感度/心情存在**双轨并行**：
> 一句话可能被新旧两套逻辑各算一次，状态注入也在 system prompt 里重复了两遍。
> 本版把「单一状态源 + 单一演化入口 + 单一注入出口」理顺。

### 改动清单

| 文件 | 改动 |
|---|---|
| `backend/core/mood.py` | 新增 `idle_decay_if_due`（纯冷落衰减，不做关键词互动检测）；`on_user_message` 保留为兼容旧调用方的独立入口 |
| `backend/core/affection.py` | `on_message` 不再调用 `mood.on_user_message`（避免心情双算），改为 `idle_decay_if_due` 只做冷落衰减 + 读心情值做倍率缩放 |
| `backend/core/persona.py` | 移除 `mood_line` 注入，`behavior_line`（行为帧）成为唯一状态注入出口，消除「心情被说两遍」 |
| `backend/core/perception.py` | 感知结果新增 `degraded` 标记，区分「真语义成功」与「关键词降级」 |
| `backend/core/pipeline.py` | 1.1 节主从关系收敛：`semantic_ok = perc is not None and not perc["degraded"]`，真语义成功跳过关键词每日奖励，降级/失败才兜底 |

### 主从关系（唯一约定）

```
用户消息
   │
   ▼
① perception.perceive(text) —— 唯一入口（LLM 语义，失败降级关键词 + 标记 degraded）
   │
   ▼
② state.apply_impulse(...)  —— 唯一演化入口（情绪 + 好感度一次性落地）
   │
   ▼
③ behavior.build_behavior_frame —— 唯一注入出口（persona 只从这里拿状态）
```

- **心情**：语义感知为主，`mood` 只提供「冷落衰减 + 天气基线 + 漂移」这类时间/环境维度，不再独立算「这句话的互动增减」。
- **好感度**：语义感知的 `affection_delta` 为主；关键词 `try_daily_bonus` 仅在感知降级/失败时兜底（补足每日奖励的正向反馈）。

---

## 三、架构与数据流

```
用户消息
   │
   ▼
① perception（LLM 语义感知）── 读懂这句话的情绪/好感影响（结构化 delta）
   │                          └─ 失败降级 → affection/mood 关键词规则
   ▼
② state.apply_impulse ── 情绪/精力/好感度/情绪记忆 持续演化，写入 SQLite
   │
   ▼
③ behavior.build_behavior_frame ── 状态 → 语气基调/主动性/情绪残留 → 行为帧
   │
   ▼
④ build_system_prompt ── persona + 行为帧 → LLM 生成（流式）
   │
   ▼
⑤ initiative（后台 loop + 待投递队列）── 没聊时生成主动消息，入队等前端取走
```

---

## 四、关键设计决策

1. **多维状态机**：情绪（0-100，向基线回归+互动扰动）、精力（0-100，越久没聊越疲惫）、好感度（沿用 users 表）、关系阶段（初识/熟悉/亲密/恋人）、情绪记忆（最近情绪冲击的短时残留，半衰期 3 小时）。

2. **状态持久化**：情绪值存 users.mood_value，情绪记忆存 kv_store，跨天连续、重启不丢。

3. **LLM 语义感知可降级**：感知失败/超时/无 key 时自动回退关键词规则，保证对话不卡、行为不退化。

4. **轻量主动、不骚扰**：只在「关系够近（熟悉以上）+ 久未聊（6h）+ 今天没主动过」时主动，每用户每天最多 1 次，全局冷却 15 分钟。

5. **避免双重计分（主从关系）**：真语义成功（`perc` 存在且 `degraded=False`）时，旧的关键词每日奖励（关心/道歉/分享/夸）跳过，只保留语义无法覆盖的「用称呼交流」「引用记忆」；语义降级（`degraded=True`）或完全失败（`perc=None`）时才走关键词兜底。心情同理：`affection.on_message` 只做冷落衰减，不再重复做关键词互动检测。

---

## 五、运行方式

```powershell
# 后端（现有启动方式不变）
python -m backend.main

# 新核心层测试
MEMORY_EMBED_FORCE=1 MEMORY_V2=0 .\.venv\Scripts\python.exe tests\test_anthropic_core.py
# 或 bash:
MEMORY_EMBED_FORCE=1 MEMORY_V2=0 ./.venv/Scripts/python.exe tests/test_anthropic_core.py

# 主动消息轮询端点（前端定时调用）
GET /api/initiative?session_id=<id>
```

---

## 六、已知限制与后续建议

1. **语义感知的 token 成本**：每条用户消息多一次 LLM 调用（约几百 ms + 少量 token）。已做降级兜底，但若要极致省钱，可在 `.env` 加开关把语义感知关掉、只走关键词（预留了降级路径）。

2. **主动性「推」模式 + 桌面实时通知（已实现）**：后台 loop 定时生成主动消息 → 写入 kv_store 待投递队列（离线不丢）→ 前端 30s 轮询 `GET /api/initiative` 取走。前端拿到消息后：① 追加为聊天气泡；② 若窗口隐藏（`document.hidden`）则弹 Electron 系统通知，点击通知聚焦窗口。用户关掉窗口（隐藏到托盘）后菟菚仍能通过系统通知冒出来。

3. **精力维度尚未接天气/深夜完整建模**：当前精力由「距上次互动时长 + 深夜」推导，未引入完整昼夜节律。可作为下一轮增强。

4. **情绪记忆仅 6 条短时残留**：够让「还在闹别扭/还开心」成立，但不做长期情绪档案。如需「她记得你上周让她难过了」，可扩成长期情绪记忆。

---

## 七、后续可做的方向（按优先级）

> 以下四项已在 v4 全部落地，见下方「v4」章节。

---

## 八、v4 增强：SSE 长连接 + 独立小模型 + 昼夜节律/天气 + 长期情绪档案

> 本版完成四项增强，把「拟人」从「能演」推向「更像一个持续存在、有生理节律、有长期记忆的人」。

### 改动清单

| 模块 | 文件 | 改动 |
|---|---|---|
| **SSE 长连接** | `backend/core/initiative.py` | 新增订阅/发布（`subscribe`/`unsubscribe`/`_notify_subscribers`）+ `sse_event_stream` 事件流（首帧秒级推送 + 心跳保活 + 队列兜底探测） |
| | `backend/api/initiative.py` | 新增 `GET /api/initiative/stream`（SSE 端点），保留旧 `/api/initiative` 轮询兼容 |
| | `frontend/src/api/sessions.ts` | 新增 `openInitiativeStream`（EventSource 封装） |
| | `frontend/src/components/ChatView.vue` | 用 EventSource 长连接替代 30s 轮询，切会话自动重建连接 |
| **独立小模型** | `backend/core/config.py` | 新增 `LLM_PERCEPTION_*` 配置组（独立模型/端点/key/超时） |
| | `backend/core/llm.py` | `chat` 支持 `perception=True` 走独立 client + 模型；新增 `get_perception_client` |
| | `backend/core/perception.py` | `perceive` 走感知层独立小模型通道 |
| | `.env.example` | 补充感知层独立模型配置说明 |
| **昼夜节律/天气** | `backend/core/state.py` | `_derive_energy` 重写为「昼夜节律折线 + 互动衰减 + 天气联动」三段式；新增 `_interp_circadian`/`_weather_energy_offset` |
| **长期情绪档案** | `backend/core/state.py` | 新增长期情绪档案（`record_emotion_archive`/`recall_emotion_archive`/`_decay_archive`），半衰期 30 天、按主题聚合；`apply_impulse` 自动归档；`AgentState` 携带 `emotion_archive` |
| | `backend/core/behavior.py` | `BehaviorFrame` 新增 `archive_line`（长期态度），`_archive_line` 把长期情感积累转成「更防备/更亲近」的自然语言 |

### 各增强要点

**1. SSE 长连接（秒级推送）**

```
后台主动性 loop 生成消息 → enqueue_proactive（入队 + 广播订阅者）
                                   │
                                   ▼
前端 EventSource 订阅 /api/initiative/stream ── 秒级收到 event: initiative
（另带首帧清队列 + 每 15s 心跳保活 + 每 10s 队列兜底探测）
```

- 从「最多 30s 延迟」降到「秒级」，菟菚主动开口几乎实时送达。
- 保留旧轮询端点，兼容不破坏；SSE 断线由 EventSource 自动重连。

**2. 独立小模型感知层**

- 语义感知是「每条消息一次」的高频调用，用独立轻量模型可显著降延迟/成本。
- `.env` 配 `LLM_PERCEPTION_MODEL`（如 `Qwen/Qwen3-8B`）+ 可选独立 `BASE_URL`/`API_KEY`/`TIMEOUT`；留空则复用主 LLM，行为不变。

**3. 精力昼夜节律 + 天气联动**

- 精力从「距上次互动 + 深夜」升级为「完整 24h 节律曲线」：清晨 6 点最低（刚醒困）→ 午前爬升 → 午后 14 点小低谷（饭后犯困）→ 傍晚 19 点高峰 → 深夜回落。
- 天气联动：阴雨/霾/雷等压抑天气让精力基线下降，晴朗上升（复用 mood 的天气日缓存，不额外发请求）。

**4. 长期情绪档案**

- 短时情绪记忆（3h 半衰期）只解决「还在不在刚才的情绪里」；长期档案解决「她记得你上周让她难过、这个月总夸她」。
- 按主题聚合（`count` 累计、`weight` 叠加）、半衰期 30 天、封顶 20 主题、召回 top3。
- 注入行为帧成为「长期态度」：负向积累占主导 → 更防备、不轻易被一句软话哄好；正向积累显著 → 更信任、更愿亲近。这是「接近正常人」的关键——对一个人的态度是长期积累的，不是每句话独立算。

### 验证

| 项 | 结果 |
|---|---|
| 拟人核心层测试 `tests/test_anthropic_core.py` | **41 项全通过**（新增昼夜节律 7 项 + 长期档案 5 项 + SSE 1 项） |
| 心情规则测试 `test_mood_rules.py` | 4 项全通过（无回归） |
| P4 系统测试 `test_p4_system.py` | 5 项全通过（无回归） |
| 前端 `vue-tsc` 类型检查 | 通过（0 错误） |
| 前端 `vite build` 生产构建 | 成功 |
| 前端 `vitest` | 8 项全通过 |

---

## 九、已知限制与后续建议（v4 后更新）

> 注意：本节的 1、2、3 三条在此前的迭代中已陆续落地，这里保留原始描述作为演进记录，
> 实际状态见下方「十一、v6 收尾」。

1. ~~SSE 依赖前端 EventSource 保持连接~~ → **已解决**：主进程独立轮询（`electron/main.ts` 的 `setInterval(pollInitiative, 30s)`），关窗也能弹系统通知。

2. ~~独立小模型需自配~~ → **已配好**：`.env` 里 `LLM_PERCEPTION_MODEL`/`BASE_URL` 已填，key 自动回退 `IMAGE_API_KEY`。

3. ~~长期情绪档案不做点名翻旧账~~ → **已补**：新增「事件级长期记忆」（`state.event_memory` + `behavior._event_line`），带原文、可精确引用，且克制「不翻旧账」。

4. **天气联动受 `MOOD_CITY` 约束**：未配置 `MOOD_CITY` 时精力只剩昼夜节律 + 互动衰减（天气偏移返回 0），节律本身已完整。

---

## 十、v5 细节打磨：识图语气 + 天气匹配 + 问候轮换 + 辱骂词变体

> 上一轮整体架构已稳，这一轮清理四处「细节粗糙」——都是不影响主流程、但会影响真实感和覆盖面的小问题。

### 改动清单

| 文件 | 问题 | 修复 |
|---|---|---|
| `backend/core/vision.py` | 识图 prompt 写死「3~6 句话」，没结合菟菚人设 | prompt 改为「你是菟菚，一个腹黑毒舌的女孩子」，要求用自己的语气说图：信息不丢（主体/场景/文字/情绪），但像随口聊天、带点调侃，不写说明书 |
| `backend/core/mood.py` | `_weather_via_search` 靠关键词子串匹配，单字「风」误配「风格」「风云」 | 单字天气词改为「天气上下文确认」：前后紧跟天气指示字（转/有/小/阵/雷阵/级/℃…）或整段含天气语义标记（气温/风力/湿度/降水…）才认；多字词（沙尘/多云）直接匹配 |
| `backend/core/greeting.py` | 兜底问候写死一句，重复率高 | 改为 8 句轮换池 + 随机抽取 + 记录上次索引避免连续重复 |
| `backend/core/affection.py` | 辱骂词库仅 12 词，手工扩充跟不上 | 运行时自动展开：脏字根×尾缀组合 + 谐音/拼音缩写整词表 + 英文词，统一词界匹配 |

### 关键设计说明

**1. 识图语气（vision）**

识图是「菟菚替自己看图片」，不是「系统出图片说明」。prompt 点明她的身份和语气后，视觉模型输出的描述会自然带毒舌/调侃味，后续再进对话也不会显得突兀。

**2. 天气匹配（mood）——本轮最讲究的一处**

一开始试了「单字前后无汉字才算天气」的词界法，结果把「今天晴」「小雨」「有风」全误杀了——天气词前面跟普通汉字太正常（晴天/小雨/大风/转阴）。最终改成「天气上下文确认」：

- 真实天气文本几乎必带上下文（「晴，气温 25℃」「多云转阴」「风力 3 级」），所以靠上下文认单字既准又不会漏；
- 「风格」「风云」「雷军」「阴间」「雾化」这类固定复合词/人名，前后没有天气指示字，自然被排除。

**3. 辱骂词变体（affection）——从手工堆词到自动展开**

- 脏字根（傻/逼/贱/操/妈/鸡/死/狗/滚/脑/废，各带同音近音字）× 尾缀（逼/比/狗/货/蛋…）笛卡尔展开；
- 叠加谐音整词（沙雕/二逼/狗逼/nmsl/卧槽…）、拼音连写（shabi/shadiao/wocao…）、英文词（fuck/shit/bitch）；
- 结果：词条 12 → **392**，覆盖「傻逼/沙比/shabi/SB/狗逼/卧槽」等常见变体；
- 防误伤双保险：英文/拼音用词界匹配（`sb` 不误配 `asb`），中文用白名单排除（「垃圾分类」「风格」不误伤），另有对象词排除（「sb同事」骂的是别人，不扣菟菚好感）。

### 验证

| 项 | 结果 |
|---|---|
| 拟人核心层测试 `tests/test_anthropic_core.py` | **41 项全通过** |
| 辱骂词变体冒烟 | 392 词条；shabi/SB/狗逼/nmsl 全命中；垃圾分类/风格/sb同事 无误伤 |
| 天气匹配冒烟 | 风格/风云/阴间/雷军/雾化 全排除；晴/雨/雷/风 正常命中 |
| 问候轮换冒烟 | 8 句池，连续两次抽取不重复 |

### 附带修复

`tests/test_anthropic_core.py` 的 `test_emotion_archive` 补了数据隔离（清理旧档案），修复了「同主题聚合」在测试重复运行时因 kv_store 残留导致的偶发失败（这是上一轮遗留的测试缺陷，非本轮功能回归）。

---

## 十一、v6 收尾：三条「已知限制」核对与固化

> 用户把 v4 交付文档「九、已知限制」里的三条列出来，本意是「这些该做了」。
> 核对代码后发现：**这三条在此前的迭代中其实都已落地**，只是文档没及时更新、测试没固化。
> 本轮做的是「核对 + 补测试 + 补文档」，没有新增功能代码。

### 核对结论

| # | 条目 | 实际状态 | 本轮动作 |
|---|---|---|---|
| 1 | 事件级长期记忆（带原文，可点名引用） | ✅ 已实现（`state.event_memory` + `behavior._event_line` + `record_event_memory`/`recall_event_memory` + 测试 6 项） | 核对确认，无新代码 |
| 2 | 独立小模型真正生效 | ✅ 已生效（`.env` 已配 `LLM_PERCEPTION_*`，`get_perception_client` 自动回退 `IMAGE_API_KEY`） | 补 `test_perception_client` 6 项固化回退逻辑 |
| 3 | 关窗也实时弹通知 | ✅ 已实现（`electron/main.ts` 主进程 `setInterval(pollInitiative, 30s)` + `set-active-session` IPC + `initiative-message` 转发 + 前端去重） | 核对确认，vue-tsc/vite build/vitest 全通过 |

### 实现细节（供后续维护参考）

**1. 事件级长期记忆与「长期情绪档案」的分工**

- `archive`（情绪档案）：按**主题**聚合（`count`+`weight`），只影响「长期态度基调」（更防备/更亲近），**不点名**。
- `event_memory`（事件记忆）：带**原文**（`text`，截断 120 字）+ 时间戳 + 正负向，强冲击（`weight >= 0.6`）才记，半衰期 30 天，召回 top3，`behavior._event_line` 把它转成「她记得你上次说过某句话」的自然提示。
- 关键克制：负向事件只「在心里留痕迹、不主动翻旧账」；正向事件「合适时自然带一句、别复述原话」。

**2. 独立小模型的 key 回退链**

```
LLM_PERCEPTION_API_KEY → IMAGE_API_KEY（硅基流动生图同 key）→ LLM_API_KEY
```

`get_perception_client()`：只要 `llm_perception_model` 或 `base_url` 任一非空，就走独立 client；否则退回主 client。`.env` 里已填 `LLM_PERCEPTION_MODEL=Qwen/...` 和 `BASE_URL`，key 靠回退链拿到 `IMAGE_API_KEY`。

**3. 关窗弹通知的双通道 + 去重**

- 窗口开着：渲染进程 SSE 秒级收到 → 追加气泡（`startInitiativeStream`）。
- 窗口关着/隐藏：主进程 `pollInitiative` 每 30s 轮询 `/api/initiative` → 主进程直接弹系统通知（`Notification`），点击聚焦窗口。
- 去重：前端 `notifiedKey`（取消息前 40 字）+ 主进程 `lastNotifiedText`，避免双通道重复弹。

### 一个已知小缺口（非 bug，留作后续）

`pipeline.py` 调用 `apply_impulse` 时只传了 `text`（用户原文），**没传 `reply`（菟菚当轮回复）**——因为 `apply_impulse` 在 LLM 生成回复**之前**执行，此时 reply 还没产出。`event_memory` 的 `reply` 是预留字段，当前 `_event_line` 只消费 `text`，故不影响功能。若将来要「她记得自己当时怎么回的」，需在回复生成后回填 `reply`（增一次轻量 `record_event_memory` 回填或加 `patch_event_reply`）。

### 验证

| 项 | 结果 |
|---|---|
| 拟人核心层测试 `tests/test_anthropic_core.py` | **52 项全通过**（新增 `test_perception_client` 6 项） |
| 前端 `vue-tsc` | 通过（0 错误） |
| 前端 `vite build`（含 electron 主进程 main.js/preload.mjs） | 成功 |
| 前端 `vitest` | 8 项全通过 |
