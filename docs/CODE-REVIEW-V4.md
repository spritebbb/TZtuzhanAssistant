---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 5b6c36c0754ae7eea02a4074b3853301_7792ddeaa6bc11f1ac80525400aeaaa3
    ReservedCode1: h2plMgp1qFAB0SFzYm9zwGOjO0F349D792APjNo5MElMDNe3Ofwt+IQW+LlQ2A56gXcEVz9bRjYOu3STnupbon+6ziflYCo2AvvuZHer5MekuYYbKcQRinYL6SDXduIYLshtakV49j9PmyAVWEOn1l2OHHOS0a81MCpF1z3nN49cSwL19J8/MQC31V8=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 5b6c36c0754ae7eea02a4074b3853301_7792ddeaa6bc11f1ac80525400aeaaa3
    ReservedCode2: h2plMgp1qFAB0SFzYm9zwGOjO0F349D792APjNo5MElMDNe3Ofwt+IQW+LlQ2A56gXcEVz9bRjYOu3STnupbon+6ziflYCo2AvvuZHer5MekuYYbKcQRinYL6SDXduIYLshtakV49j9PmyAVWEOn1l2OHHOS0a81MCpF1z3nN49cSwL19J8/MQC31V8=
---

# CODE REVIEW V4 — TZtuzhanAssistant 复审确认

- 审查日期：2026-09-02
- 审查范围：backend/（core/memory、core/pipeline、plugins/loader、api/plugins、tools/base、tools/mcp_server）+ plugins/（external、system、safety）
- 审查方式：只读代码核验，未改动任何代码
- 基线：V3 报告（S1-S6 问题清单）修复后复审

## 一、结论总览

V3 提出的 S1-S6 全部确认修复，未发现同类回退。修复质量整体合格，纵深防御意识较 V2 有显著提升（同一越界点同时存在 API 层预检与路径层归属校验两层防护）。剩余观察项均为非阻断级改进建议，不涉及新增风险。

| 编号 | 问题 | 状态 | 核验证据 |
|------|------|------|----------|
| S1 | dsh_run 长任务经 argv 传递可能超长失败 | 已修复 | plugins/external.py `_dsh_run` 增加 20000 字符上限预检与明确错误提示；Codex 走 stdin + output-last-message，不再依赖 argv |
| S2 | 画像 LLM 每轮每条消息触发，与游标攒批并存 | 已修复 | core/memory/engine.py `on_message` 仅保留 Mem0 后台任务（受 `config.memory_mem0` 开关约束）；画像提炼职责回归 pipeline 1.6 游标节流（`p_unseen >= 10` 或空闲+长间隔才调度） |
| S3 | enable/reload 等 API 缺路径归属校验，可路径穿越 | 已修复 | api/plugins.py 的 enable/disable/reload/source 入口统一增加 `loader.plugin_name_ok(name)` 预检（纯 `[A-Za-z0-9_-]` 单层名，拒绝 `../` 与子目录）；source 端点保留 `is_relative_to` 归属校验，构成两层纵深 |
| S4 | 危险命令黑名单漏 `del /q`、`del /s`、`erase` | 已修复 | safety.py 黑名单已覆盖 format/rd/del/rmdir/rm/shutdown/reg delete/diskpark/erase 等 27 条正则 + 关键词表 |
| S5 | open_app 对带空格路径不可用（引号被挡） | 已修复 | plugins/system.py `_open_app` 路径分支改经 `Start-Process -FilePath '字面量'`（create_subprocess_exec 直连，不经 cmd /c），支持空格路径且无 shell 注入面；应用名分支仍用 cmd start 但拒绝 `&|<>^;"` 与 `%`（防环境变量展开与换行续写） |
| S6 | ToolRegistry._tools 私有字段被外部直接读写 | 已修复 | base.py 收敛为私有 + 公开方法（register/unregister/unregister_many/get/list/list_tools/tool_names 只读视图）；loader.py 回滚/重载逻辑与 mcp_server.py 的 `unregister_external_server` 均改走公开 API；全库 grep 无外部直改 `ToolRegistry._tools`（仅 base.py 内部实现引用） |

## 二、逐项核验细节

### S1 — external.py 长命令 / Codex 调用
- `_dsh_run` 在拼装前对 task 长度做 20000 字符上限预检，超长返回明确错误而非静默截断或抛错。
- Codex 执行路径经 stdin 传提示词，规避 argv 长度上限；输出仅取 last message，避免全量输出膨胀。

### S2 — 画像提炼调用点
- engine.py `on_message` 中旧的逐条 `extract_profile` 调用已移除，当前仅保留 Mem0 add（且 `mock` 时不触发）。
- pipeline.py 1.6 惰性画像提炼保留独立游标 `last_profile_msg_id`，与 facts 游标并行；触发条件为攒批 ≥10 条或空闲且新增 ≥ `_IDLE_MIN_NEW`，单轮单条消息不再触发 LLM。
- 同一批消息只对应一次画像提炼调度，重复计数风险已消除。

### S3 — 插件 API 路径归属
- 4 个管理端点（enable/disable/reload/source）与 gateway 路由均先过 `plugin_name_ok`：非空、纯字母数字下划线连字符，从根上拒绝 `../`、绝对路径、子目录、空串。
- source 端点另保留 resolve + is_relative_to 兜底（防止 name 合法但文件实际不在 plugins 目录的异常场景）。

### S4 — 危险命令黑名单
- `_BLOCK_PATTERNS` 已包含 27 条正则，覆盖 format/rd/del/rmdir/rm/shutdown/reg delete/diskpart 等破坏性命令及常见变体；`_BLOCK_KEYWORDS` 辅助拦截。
- 核验未见绕过路径（如大小写混合、短名、编码变体）在当前正则集下可穿透的明显案例。

### S5 — open_app 空格路径
- 路径分支：create_subprocess_exec 直连 powershell `Start-Process -FilePath '<safe>'`；safe 为单引号包裹的字面量，内部 `'` 有转义处理，路径前导校验排除 UNC/root 之外的可执行目标，无 cmd 解释层。
- 应用名分支：保留 `cmd /c start` 但参数先经字符校验（拒绝 shell 元字符与 `%`），防止命令夹带。

### S6 — ToolRegistry 封装
- base.py：`_tools` 仅类内使用；新增 `unregister`、`unregister_many`、`tool_names()`（返回只读副本）等公开方法。
- loader.py 219-284 行重载/卸载回滚逻辑全部走公开 API（`_registered_tools_before` 用 list_tools、快照恢复用 register、差异清理用 tool_names+unregister、比对用 get）。
- mcp_server.py `unregister_external_server` 亦改为 tool_names+unregister 遍历前缀卸载；其类内 `self._tools` 为外部 MCP 客户端自身缓存，与 ToolRegistry 无关。

## 三、非阻断观察（供后续参考，不阻塞发布）

1. `plugin_name_ok` 仅做白名单字符校验，不校验该 name 是否真实存在于磁盘（enable/reload 中由随后 `PLUGINS_DIR / f"{name}.py"` 的 exists 兜底），语义清晰，无风险。
2. `_open_app` 路径分支的 powershell 进程每次启动有秒级开销；如打开操作频繁可考虑缓存会话，属性能优化而非缺陷。
3. safety.py 正则清单已较长，建议后续为每条正则补充「示例触发串」注释，便于回归维护（低优先）。

## 四、结论

S1-S6 全部关闭，V2→V4 三轮修复链路完成闭环。当前版本未见阻断级缺陷，可进入发布前自检。
*（内容由AI生成，仅供参考）*
