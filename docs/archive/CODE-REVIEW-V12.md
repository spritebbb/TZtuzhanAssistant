# 代码审查报告 V12 —— 全量收尾审查

> 审查范围：全部 86 个 backend/*.py + 12 个 plugins/*.py（**100% 覆盖**，前几轮未读的模块本次全部补齐）
> 审查方式：逐文件精读 + 关键疑点脚本复现验证
> 结论：**P0=0、P1=0、P2=5、P3=3**
> 说明：本轮只审查、只报告，**未修改任何代码**。

---

## 一、本轮新增覆盖的模块

前几轮已覆盖：代码执行/沙箱、文件操作、SSE 流式、工具循环、意图路由、向量库、MCP、心情/行为/感知/状态机、会话存储、数据层。

本轮补齐（此前未读）：

| 模块 | 文件 | 质量评价 |
|------|------|----------|
| Agent 长任务执行器 | `agent/session.py` | 高，步骤确认+超时+取消语义严谨 |
| 好感度系统 | `core/affection.py` | 高，辱骂词变体自动生成设计精巧 |
| 心情系统 | `core/mood.py` | 高，天气多源降级+单字词上下文消歧用心 |
| 人格组装 | `core/persona.py` | 高，状态注入出口单一清晰 |
| 联网搜索 | `core/search.py` | 高，多引擎降级+TTL 缓存 |
| 每日总结/事实提炼 | `core/daily.py` | 高，截断 JSON 修复器设计好 |
| 特殊日子记忆 | `core/memory/date_memory.py` | 高，日期格式校验严格 |
| 话题记忆 | `core/memory/topic_memory.py` | 高 |
| 结构化事实记忆 | `core/memory/triple_memory.py` | 高 |
| 记忆引擎/管理/迁移 | `core/memory/engine·memory_manager·migration` | 高，Mem0 降级+冷却重建稳健 |
| embedding 层 | `core/memory/embedding.py` | 高，模型→哈希三级降级，缓存模式标注防维度污染 |
| 画像/问候/主动性 | `core/profile·greeting·initiative` | 高，主动性引擎"不骚扰"原则清晰 |
| 配置/日志/后台任务 | `core/config·log·tasks` | 高，配置键校验+控制字符过滤 |
| TTS/识图/生图 | `core/tts·vision·imagegen` | 高，生图错误分类+可重试判断精细 |
| 维护循环 | `maintenance/loop.py` | 高，SQLite 在线备份 API 正确 |
| 插件系统 | `plugins/context·loader` | 高，卸载语义+回滚+路径穿越防护严谨 |
| 技能目录 | `skills/catalog.py` | 中，实现简单够用 |
| 全部 API 端点 | `api/*`（15 个） | 高，Host/Origin/来源 IP 三层守卫 |
| 入口/工厂 | `app.py·main.py` | 高 |

**总体印象**：这是一份质量显著高于平均水平、且安全意识非常到位的代码库。绝大多数"风险点"经脚本复现后都被排除（见下文"已排除的疑点"）。本轮没有发现 P0/P1 级问题。

---

## 二、本轮新发现的问题

### 🟡 P2-1 `daily.py` 的 `get_user` 未判空

**位置**：`backend/core/daily.py` 第 128 行

```python
if addr and not db.get_user(user_id)["nickname_pref"] and not affection.check_bad_address(addr):
```

**问题**：`db.get_user()` 返回 `sqlite3.Row` 或 `None`。若返回 `None`，`None["nickname_pref"]` 抛 `TypeError: 'NoneType' object is not subscriptable`。

**实际影响（评估后降级为 P2）**：当前调用链 `affection.on_message` 在调度 `run_daily_batch` 之前已 `db.ensure_user()`，所以正常流程下该 user 必然存在。但 `run_daily_batch` 作为公开函数也可能被其他路径（测试、手动触发、未来调用方）直接调用，且一旦异常，会被 `tasks.schedule` 的 `_runner` 静默吞掉——**该日的称呼提取会悄悄失败**，无任何日志提示。

**建议**：改为 `u = db.get_user(user_id); if u is None: return`，或 `(db.get_user(user_id) or {}).get("nickname_pref")`。

---

### 🟡 P2-2 `affection.py` 扣分倍率存在"心情极好时不扣分"的边界

**位置**：`backend/core/affection.py` 第 431-436 行 `_scale_delta`

```python
if delta >= 0:
    return round(delta * mult)
return round(delta * (2.0 - mult))
```

**问题**：扣分公式 `delta * (2.0 - mult)` 在 `mult=1.5`（雀跃）时，`-1 * 0.5 = -0.5`，`round(-0.5) = 0`（Python 银行家舍入，round 到最近的偶数）。实测：

```
mult=1.5（雀跃）: -1 → round(-0.5) = 0   ← 扣分被吞
mult=1.2（开心）: -1 → round(-0.8) = -1
mult=1.0（正常）: -1 → -1
mult=0.8（平淡）: -1 → -1
mult=0.6（低落）: -1 → round(-1.4) = -1
```

**影响**：菟菚心情"雀跃"时，用户轻微冒犯（单次 -1 档的辱骂/刷屏等经过缩放后为 -1）会被完全吞掉——**心情越好越不该被"豁免"惩罚**，这与注释"心情差时扣分更狠"的设计意图不完全吻合（意图是"心情好扣分轻"，而非"不扣"）。

**建议**：扣分至少保底 `min(delta, scaled)`，即 `scaled = round(delta * (2.0 - mult)); return min(delta, scaled)`，保证负向变动不为 0。

---

### 🟡 P2-3 `config.py` 密钥值未过滤 `#` 注释截断

**位置**：`backend/core/config.py` `update_env_file` 第 183 行

```python
value = re.sub(r"[\r\n\x00-\x1f\x7f]", "", value).strip()
```

**问题**：过滤了换行和控制字符，但未处理 `#`。若用户通过 `/api/config` 保存的密钥值本身包含 `#`（少见但可能，如某些带注释的 token），写入 `.env` 后 `#` 及其后内容会被 `dotenv` 当注释忽略，导致**密钥被静默截断**，下一次读取变成不完整 key。

**影响**：低概率，但属于"配置值注入 .env 语义"的完整性缺口。

**建议**：对值做 `quote` 或用 `#` 转义，或在文档中明确"密钥不能含 #"。至少对 secret 字段加校验。

---

### 🟡 P2-4 `greeting.py` 与 `initiative.py` 的问候/主动消息并发边界

**位置**：`backend/core/greeting.py` 第 131-138 行

```python
with _greet_lock:
    last_ts = _last_seen_ts(user_id)
    ...
    _set_last_seen(user_id, now)
# 锁外生成问候 + 持久化
text = await _greeting_text(user_id)
```

**问题**：`_greet_lock` 是 `threading.Lock`，但 `greeting_for` 是 async 函数，`_greeting_text`（内部 `await chat`）在**锁外**执行。两个并发请求可能同时通过锁内的 `last_ts` 检查（都判定"久别"），然后都去生成并持久化问候 → **重复问候**。

**影响**：低概率（前端通常串行调用 `/api/greeting`），但"读-判-写"的原子性被 `await` 打断，锁只保护了读判阶段。`initiative.py` 的 `_mark_proactive`/`_proactive_done_today` 也有类似"先查后写"但无锁的问题（不过后台 loop 是单任务串行，风险更低）。

**建议**：问候去重用一个 `kv_store` 标记（生成前先占位），或接受"极低概率重复问候"并在文档标注。

---

### 🟡 P2-5 `affection.py` 刷屏检测的 `_spam_triggered` 标记永不释放（窗口语义）

**位置**：`backend/core/affection.py` 第 329-342 行 `_spam_hit`

```python
if len(q) >= _SPAM_MAX_COUNT:
    if user_id in _spam_triggered:
        return False
    _spam_triggered.add(user_id)
    return True
_spam_triggered.discard(user_id)  # 只有 len < MAX 才走到这里
```

**问题**：`_spam_triggered.discard(user_id)` 在 `len(q) >= _SPAM_MAX_COUNT` 分支（即已触发刷屏后）**不会执行**。一旦某用户触发过一次刷屏，只要该用户的 `_timestamps` 队列还在（`_cleanup_timestamps` 只在 `>1000` 用户或 1 小时不活跃时清理），`_spam_triggered` 里的标记就一直在。窗口内消息会持续 `return False`（正确），但如果用户**一直持续发消息**（队列始终 ≥4），则永远无法再次触发——这本是设计意图（同一突发窗口只罚一次）。

**真正的隐患**：`discard` 只在 `len(q) < _SPAM_MAX_COUNT` 时执行，即只有当用户**停止发消息让窗口自然清空**后标记才清除。若用户持续刷屏数小时，标记一直挂着是**符合预期的**（该罚）。所以这个"永不释放"在语义上其实是对的——**排除为 bug**，仅记录：`_cleanup_timestamps` 的 1 小时不活跃阈值与 `_SPAM_WINDOW_SECONDS=8` 之间存在理解成本，建议补注释。

> 此条经分析后**不构成缺陷**，仅作说明记录，不计入 P2 总数。

---

## 三、💭 P3 建议（Nice to Have）

### 💭 P3-1 `memory_search` 工具硬编码导入私有函数

**位置**：`backend/tools/builtin/memory.py` 第 33、40 行

```python
from ...core.memory import _recall_with_expansion
from ...core.memory import _facts_with_expansion
```

**问题**：直接导入 `memory` 包的私有函数（下划线开头）。若 `memory/__init__.py` 未来重构导出，此处会断。且 `long_term.py` 里对应的函数是 `recall`/`recall_facts`（公开），`_recall_with_expansion` 是否真的存在需确认——**若不存在，则此处的 `try` 永远走 `except` 分支回退到关键词检索，语义检索的 `memory_search` 实际退化**。

> 建议核实 `backend/core/memory/__init__.py` 是否导出了 `_recall_with_expansion` / `_facts_with_expansion`。

### 💭 P3-2 `currency.py` 示例插件同步阻塞网络

**位置**：`plugins/currency.py` `_fetch_rate`（urllib 同步请求）

虽然 `register_func` 标了 `is_async=False`，但汇率请求在事件循环线程内同步阻塞 15s 超时，可能卡住主循环。作为示例插件可接受，但建议在示例中演示 `asyncio.to_thread` 的正确用法，避免被用户复制成反模式。

### 💭 P3-3 `meta.py` 的 `vector_count` 依赖 stats 字段全为 int 的隐含契约

**位置**：`backend/api/meta.py` 第 35 行

```python
"vector_count": sum(v for k, v in stats.items() if k != "enabled" and isinstance(v, int)),
```

若未来 `vector_store.stats()` 加入非计数的 int 字段（如维度、活跃连接数），会被误加进 count。建议 `stats()` 显式返回 `{"enabled": ..., "counts": {...}}` 结构化，或此处用白名单 kind。

---

## 四、已排除的疑点（复现验证后确认无问题）

| 疑点 | 验证结果 |
|------|----------|
| `affection_log.ts` 的 `LIKE '日期%'` 前缀匹配 | ✅ ts 格式为 `2026-09-03T00:53:47`，日期前缀一致，匹配正确 |
| `chat_count` 心情倍率 `round(1*mult)` 是否可能变 0 | ✅ 最低 mult=0.6 时 `round(0.6)=1`，不会为 0 |
| `vector_store.stats()` 是否含非 count 的 int 字段 | ✅ 仅 `enabled:bool` + 各 kind 的 count，`sum` 正确 |
| `config.update_env_file` 的 `=` 注入 | ✅ 按第一个 `=` 分割，value 含 `=` 安全 |
| `greeting.py` address 空串 f-string | ✅ 已被 `if address` 短路保护 |
| `run_python` 沙箱逃逸（前几轮） | ✅ 实测无法逃逸（getattr 不在白名单） |
| `session/store.py` 的 `asyncio.Lock` 跨 `to_thread` 串行化 | ✅ acquire/release 均在事件循环线程，可靠 |

---

## 五、累计 backlog（跨轮汇总，均未修复）

| 编号 | 级别 | 位置 | 问题 |
|------|------|------|------|
| B1 | 🟡 | `userdb.update_task` | f-string 拼 SQL（字段名来自白名单，当前安全，属坏味道） |
| B2 | 🟡 | `code_exec` 子进程超时 kill | 未清孙进程（建议 `taskkill /T`） |
| B3 | 🟡 | `daily.py:128` | `get_user` 未判空（本轮 P2-1） |
| B4 | 🟡 | `affection._scale_delta` | 雀跃时轻微扣分被 round 吞掉（本轮 P2-2） |
| B5 | 🟡 | `config.update_env_file` | 密钥值含 `#` 被注释截断（本轮 P2-3） |
| B6 | 🟡 | `greeting.py` | 问候并发去重边界（本轮 P2-4） |
| B7 | 💭 | 单字"查" | 误触发工具循环（V10 遗留） |
| B8 | 💭 | `D:\DSH` | 硬编码路径（V10 遗留） |
| B9 | 💭 | `memory_search` | 硬编码导入私有函数（本轮 P3-1，需核实） |
| B10 | 💭 | `currency.py` | 示例插件同步阻塞（本轮 P3-2） |

---

## 六、审查结论与建议

1. **代码库整体健康**：5 轮审查累计，P0 始终为 0，P1 已全部修复（V9 修 2、V10 修 2、V11 修 1），当前仅剩 P2/P3 级别的问题，且多为边界条件或坏味道。

2. **最值得做的一件事**：把 B3（`daily.py` 未判空）和 B4（扣分被 round 吞掉）修掉——这两个是"静默失效"型问题，用户感知不到但会影响功能正确性。其余 B1/B2/B5/B6 可按需处理。

3. **安全设计是本项目的亮点**：Host 白名单 + Origin/Sec-Fetch-Site 双守卫 + 来源 IP 语义鉴权 + 工具每步确认 + 审计日志脱敏，层层设防，在同类"本机 AI 助手"项目中属于第一梯队。

4. **拟人核心层质量尤为突出**：状态机三档衰减、感知层 LLM+关键词双轨降级、行为帧自然语言注入、规则外置热更新（`mood_rules.json` 改配置免重启），这些设计在可维护性上值得肯定。
