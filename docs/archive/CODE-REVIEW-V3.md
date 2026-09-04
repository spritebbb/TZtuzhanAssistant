---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 5b6c36c0754ae7eea02a4074b3853301_8a016e7ba6bb11f1891f525400f8a581
    ReservedCode1: yWcuHXB6bEpRgqmSS/Wim93EMa6L/om99DulJW54mx+ka0HzvGe0EF0mPoECmV1wfkzLLYRr7vHkSnQYySwRrUsoDM1VI4MfcVA2pEKcD1OQ3XfHSvncAxJgwiTJlb8L1iiwNB3cVteVKbRc0OGDpMtAmsS+W3T9tBI+kd1RRNfRkbdW6rkrXGj8zmE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 5b6c36c0754ae7eea02a4074b3853301_8a016e7ba6bb11f1891f525400f8a581
    ReservedCode2: yWcuHXB6bEpRgqmSS/Wim93EMa6L/om99DulJW54mx+ka0HzvGe0EF0mPoECmV1wfkzLLYRr7vHkSnQYySwRrUsoDM1VI4MfcVA2pEKcD1OQ3XfHSvncAxJgwiTJlb8L1iiwNB3cVteVKbRc0OGDpMtAmsS+W3T9tBI+kd1RRNfRkbdW6rkrXGj8zmE=
---

# 菟菚桌面助手（TZtuzhanAssistant）代码审查报告（第三轮）

- 审查日期：2026-09-02
- 审查基线：第一轮 `CODE-REVIEW.md`（P1-P18）+ 第二轮 `CODE-REVIEW-V2.md`（P0/P1/P2）+ Codex 修复后复审（R1-R6）
- 审查方式：在已修复基线上逐文件读码交叉验证，全程只读，未改动任何文件
- 范围：`backend/app.py`、`backend/tools/safety.py`、`backend/tools/mcp_server.py`、`backend/api/plugins.py`、`backend/api/chat.py`、`backend/core/pipeline.py`、`backend/core/memory/engine.py`、`backend/core/memory/memory_manager.py`、`backend/plugins/loader.py`、`plugins/external.py` 等
- 结论：R1-R6 已全部关闭（其中 R3/R6 存在"半修复"残留，见 S1/S2）；新发现 6 项待处理项（S1-S6），均建议由 Codex 修复

---

## 一、R1-R6 修复状态核对（第三轮复核）

| 编号 | 结论 | 复核依据 |
|---|---|---|
| R1 鉴权覆盖盲区 | ✅ 已修复 | `app.py` `_remote_auth_guard` 覆盖 `/plugins/`、`/api/plugins/`、`/mcp/`、`/api/mcp/`、`/api/agent/` 及 `/api/*` 写方法；统一调用 `remote_token_ok_by_peer`（来源 IP 回环免 token 语义，与绑定地址解耦）；`main.py` 对非回环绑定无 token 打启动警告 |
| R2 fallback 无清理 | ✅ 已修复 | `memory_manager.py` `_prune`（超龄 + 每用户上限）、连续 3 次失败降级阈值、冷却后自动重建 Mem0；fallback 读写按 user_id 隔离 |
| R3 codex argv 32K 阈值 | ⚠️ 部分（残留见 S1） | `external.py` codex 已改经 stdin 传 prompt，但同文件 dsh_run 的 task 仍经 argv 传给 CreateProcess——修复只覆盖 codex，注释宣称"统一经 stdin"与实现不符 |
| R4 stderr 直拼 | ✅ 已修复 | `_clean_stderr` 过滤 + 截断 + 明确 label（stderr 非任务输出） |
| R5 插件卸载按名误删 | ✅ 已修复 | `loader.py` 卸载按对象身份（owned）+ overridden 快照精确恢复，不再按名批量删除 |
| R6 双 LLM 开销 | ⚠️ 部分（残留见 S2） | pipeline 画像通道仍在每轮每条消息触发 LLM 提炼，未完全节流 |

**无回退确认**：统一鉴权后 `/api/remote`、`/mcp/*` 均复用 peer 语义；插件 v2 热装卸、记忆引擎后台任务、fallback 遗忘策略与上轮结论一致，未见回退。

---

## 二、本轮新发现问题（S1-S6，全部只读待确认后修复）

### S1（中）｜ `plugins/external.py:184-185` ｜ dsh_run 的 task 仍经 argv，Windows 命令行长任务会失败

**现状**：codex 修复时（133-142 行注释"统一经 stdin 传任务描述最稳"）只改了 `_codex_run`；`_dsh_run` 仍是：

```python
proc = await asyncio.create_subprocess_exec(
    "node", dsh, "--profile", profile, task,
    ...
)
```

**影响**：Windows `CreateProcess` 命令行上限 32767 字符，task 超长直接抛 `OSError`，被 213 行捕获后返回"（DSH 执行失败：...）"，长任务不可用且报错无上下文。与同文件注释宣称的"统一经 stdin"自相矛盾。

**修复方向**：与 codex 一致——DSH CLI 支持 stdin 时改经 stdin；若不支持，加长度阈值检测并返回友好错误（明确提示超长），而非裸 OSError。

### S2（中）｜ `backend/core/memory/engine.py:78-92` + `backend/core/pipeline.py:1.6/6.2` ｜ 画像提炼双通道并存，每轮每条消息触发 LLM，未节流

**现状**：
- pipeline 1.6 惰性画像：游标（`last_profile_msg_id`）+ ≥10 条 unseen 才触发一次批量提炼（`profile.py:97-160`，含 done 游标推进），是有节流的设计；
- pipeline 6.2 `engine.on_message`：**每条用户消息**都 `_spawn(_extract_profile_task(...))` → `fact_extractor.extract_profile`（一次 LLM），无游标、无批量、无节流；开 `memory_mem0` 时再叠加 `_mem0_add_task`（Mem0 内部还有 LLM 抽取）。

**影响**：普通对话每轮固定 2 次 LLM 调用（主回复 chat + 画像提炼）；开 Mem0 则最多 3 次。1.6 想做的"攒批省调用"被 6.2 完全旁路——这是 R6"双 LLM 开销"未完全关闭的残留。且单条消息提炼只见 1 条上下文，画像质量不如批量提炼。

**修复方向**：二选一：
1. engine 侧画像提炼改为与 1.6 共用同一游标（或直接移除，画像职责归 1.6，engine 只保留 Mem0 add）；
2. engine 画像提炼加最小间隔/批量门槛（如复用 `last_profile_msg_id` 判断 unseen ≥ N 才提炼）。

### S3（中-低）｜ `backend/api/plugins.py:31-34,57-58` ｜ enable / reload 未做路径归属校验，name 可越目录加载 .py

**现状**：`api_plugin_source`（76-80 行）已用 `resolve() + is_relative_to(plugins_root)` 防路径穿越；但 `api_plugin_enable` / `api_plugin_reload` 直接：

```python
if not (loader.PLUGINS_DIR / f"{name}.py").exists():   # name="../evil" → 项目根 evil.py
loader.load_plugin(loader.PLUGINS_DIR / f"{name}.py")
```

`loader.load_plugin(path)`（loader.py:191）无目录归属校验，`spec_from_file_location` 加载并执行任意路径含 `register()` 的 .py。

**可利用面评估**（不夸大）：enable/reload 是写方法，受统一鉴权（非回环需 token）+ Origin 守卫（cross-site 403）保护；真正落地需攻击者已能在项目根放置/复用含 `register()` 的 .py——此时直接写 `plugins/` 更直接。因此属**防御纵深 + 同文件不一致缺陷**，不是独立可利用的 P0/P1。

**修复方向**：与 source 端点一致，enable/reload 先 `resolve()` + `is_relative_to(PLUGINS_DIR)` 再放行。

### S4（低）｜ `backend/tools/safety.py` ｜ 危险命令黑名单漏 `del /q`、`del /s`（无组合）、`erase` 别名

**现状**：已拦 `del /f`、`del /q /f`、`del /s /q`、`rd /s`、`rd /q`、`rmdir /s`、`rm -rf/-r`；漏：
- `del /q *.tmp`（静默删除，无确认提示——`/q` 单独不拦）；
- `del /s *.bak`（递归删除，无 `/q` 组合不拦）；
- `erase`（del 的 cmd 内建别名，完全未覆盖）。

**影响**：`run_command` 的 cmd 内建分支把整条命令交给 `cmd /c`，通配符由 cmd 展开——上述命令可静默/递归删除。工具本身 high + needs_confirm（确认卡片兜底），属纵深缺口。

**修复方向**：补 `del\s+/q`、`del\s+/s`、`erase` 及 `erase\s+/(q|s)`。

### S5（低）｜ `plugins/system.py` open_app ｜ 带空格路径的应用无法打开（可用性局限）

**现状**：字符过滤拒绝引号（`"`），`cmd /c start "" <path>` 对含空格路径必须引号包裹；过滤后带空格的 `C:\Program Files\xxx.exe` 会被 start 解析错乱或失败。

**修复方向**：白名单场景下对 open_app 的路径参数显式 `shlex.quote` 包裹（该参数语义已限定为"启动目标"，非自由命令），或改经 `os.startfile` 类 API。

### S6（低-设计）｜ `backend/tools/base.py` ｜ ToolRegistry 私有字段被外部模块直接读写

**现状**：`loader.py`（插件装卸）与 `mcp_server.py`（`unregister_external_server` 直接 `ToolRegistry._tools.pop(...)`）都绕过注册表 API 操作 `_tools` 私有字典；卸载/注销逻辑分散在多个文件。

**影响**：后续若给注册表加并发锁、订阅、审计会漏改调用方；现无并发 bug，属结构性风险。

**修复方向**：收敛为 ToolRegistry 公开方法（`register/unregister/unregister_prefix`），外部模块一律走方法。

---

## 三、做得好的地方（本轮确认，未改动）

- 统一鉴权中间件按"来源 IP 回环"判定后，`/api/remote`、`/mcp/*`、`/api/plugins/*`、`/plugins/*` 行为一致，无历史遗留的分叉判断
- 插件 v2 owned/overridden 快照机制在多次热装卸后仍精确恢复，上轮 R5 修复无回退
- memory fallback 的"3 次降级 + 冷却重建 + 每用户上限 + 超龄清理"完整，R2 修复质量好
- `mcp_server.py` 外部服务器注册经 `check_url`（公网白名单）防 SSRF + 原子持久化 + 重启自动恢复且失败保留登记，链路自洽

---

## 四、修复优先级建议

1. 建议优先：S2（画像双通道，直接关系到每轮 LLM 成本翻倍）
2. 其次：S1（与已修复的 codex 路径对齐，改动小）
3. 纵深批次：S3 / S4（各补几行校验/正则）
4. 低优先级：S5 / S6

（按审计修复分离原则：以上全部为待确认建议，未改动任何代码。确认后可由 Codex 修复并回归。）
*（内容由AI生成，仅供参考）*
