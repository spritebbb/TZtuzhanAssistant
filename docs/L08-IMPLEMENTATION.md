# L08 统一 VisualState 与界面收纳

执行：Codex，2026-09-09。按用户指示直接实现，未派发 ZCode，节点前未运行全量聚合。

后端新增只读 `core/presence.py`，从既有情绪/精力状态、双维关系、静态行程、外出事件和专注活动生成统一 `VisualState`。`/api/meta` 与 `/api/presence` 输出同一结构，同时保留旧字段。输出只含可解释的枚举与标签，不含 prompt 或活动正文。动效偏好写入现有人格 `settings.json`，无新业务表。

前端新增单例状态源：同人格并发请求去重，revision 单调，切换人格后丢弃迟到响应，卸载时清理轮询和系统 reduced-motion 监听。安静状态统一压制装饰动画；专注和用户关闭动效都会进入安静状态。

简洁界面默认开启：顶部保留侧栏、人格、活动、更多四个主入口；其他已有功能进入可搜索“更多”，最近使用只在 localStorage 记录功能 ID。聊天顶部不再呈现好感数字；成长页继续展示关系数值。工具状态全正常时不渲染状态条，仅异常项以可点击红色提示展示原因。“本轮不留痕”移入快捷指令面板，底行只留当前输入状态。关闭 `compact_ui_enabled` 后恢复旧顶部工具入口和完整状态布局。

针对性验证：后端 presence、presence_api、life_templates、persona_switcher、flags_http、http_endpoints 共 6 个脚本通过；前端 VisualState、ToolBar、ChatInput、ChatView、DashboardPanel 与 App 首屏共 12 项测试通过，`vue-tsc --noEmit` 通过。未运行全量聚合或完整 e2e；后续 Q5 继续做浏览器缩放、对比度和完整键盘路径验收。

自行补充的决策：系统 reduced-motion 只在浏览器端合并，因为后端无法可靠读取客户端媒体查询；后端 `reduced_motion` 表示人格级用户设置。旧 `/api/presence` 字段保留，避免现有生活事件消费者断裂。

下一批：批次 12 L16 + P3-05 + §17，继续只做主题内针对性验证。
