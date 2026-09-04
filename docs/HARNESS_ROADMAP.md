# Harness 级能力映射路线图

> 目标：让菟菚桌面助手达到 DeepSeek Harness 一般的智能体能力。
> 所有能力通过 MCP 工具层暴露，由 LLM 自动调用。

## 能力映射表

| Harness 能力 | 菟菚当前状态 | 目标 | 优先级 |
|---|---|---|---|
| **文件工具**（read/write/edit/glob/grep） | ✅ read/write/list_dir/glob/grep/edit | ✓ 已达标 | — |
| **代码执行**（run_python/run_command） | ✅ run_python/run_command | ✓ 已达标 | — |
| **网络搜索**（web_search） | ✅ web_search（原生 FC） | ✓ 已达标 | — |
| **网页抓取**（web_fetch） | ✅ web_fetch | ✓ 已达标 | — |
| **MCP 协议**（/mcp/tools + /mcp/call） | ✅ 已实现 | ✓ 已达标 | — |
| **外部 MCP 服务器接入** | ✅ 注册/列表/删除 | ✓ 已达标 | — |
| **任务/目标追踪**（goal + todo） | ✅ todo 工具（原生 FC 调用） | ✓ 已达标 | — |
| **子代理编排**（subagent + workflow） | ✅ agent_run / agent_fanout | ✓ 已达标 | — |
| **记忆检索**（memory 系统） | ✅ memory_search / memory_add（原生 FC） | ✓ 已达标 | — |
| **技能系统**（skills catalog） | ✅ 技能目录 + 关键词匹配注入 + skill_search/skill_load | ✓ 已达标 | — |
| **插件系统**（插件目录自动发现） | ✅ v2：自动发现 + 热加载 + 元信息 + 管理 API/界面 + 快照恢复 + 定时任务/钩子/路由；**全部工具已插件化**（人格/记忆/好感度保留内置） | ✓ 已超额达标 | — |
| **视觉能力**（vision-bridge） | ✅ 识图（视觉模型） | ✓ 已达标 | — |
| **会话管理** | ✅ 多会话 CRUD + 搜索 | ✓ 已达标 | — |

## 迭代顺序

| 迭代 | 聚焦 | 交付物 |
|------|------|--------|
| v3 | 工具链补全 | glob/grep/edit 文件工具 + 端到端验证 |
| v4 | 任务目标追踪 | todo 工具：创建/更新/列表/完成/详情 |
| v5 | 子代理编排 | subagent 工具：派生子任务 + 收集结果 |
| v6 | 记忆强化 | 语义搜索 + 跨会话检索 + 主动记忆回顾 |
| v7 | 原生函数调用 | 从 ````tool {json}```` 文本协议升级为 OpenAI Function Calling API（todo/memory/web 全链路自动调用） |
| v8 | 技能系统 | skills/*.md 技能目录 + 关键词自动匹配注入 + skill_search/skill_load 工具（LLM 并行搜索验证通过） |
| v9 | 插件系统 | plugins/*.py 自动发现 + 启动加载注册工具（currency 示例插件验证通过） |
| v10 | 插件系统 v2 | 快照恢复修复卸载残留（CODE-REVIEW #15）+ PLUGIN_META 元信息 + 管理 API/前端界面 + 定时任务/系统提示/HTTP 路由/消息钩子（docs/PLUGIN-SYSTEM-V2.md） |
| v11 | 工具全面插件化 | 11 个工具模块迁入 plugins/ 统一管理（memory 保留内置），register_all 收敛，见 docs/PLUGIN-SYSTEM-V2.md 第五节 |

## 插件开发规范

在项目根 `plugins/` 目录放一个 `.py` 文件，暴露 `register(ctx)`（或无参 `register()`，v1 兼容）。
`ctx` 为 PluginContext，可注册：工具（`ToolRegistry.register_func`）、定时任务
（`ctx.schedule(interval, fn)`）、系统提示注入（`ctx.on_system_prompt(fn)`）、
HTTP 路由（`ctx.route(method, path, handler)` → `/plugins/{插件名}/…`）、
用户消息钩子（`ctx.on_user_message(fn)`）、回复钩子（`ctx.on_reply(fn)`）。
可选 `PLUGIN_META = {name, version, description, author}` 供管理界面展示。
启动时自动加载，改文件热重载；设置面板「插件」区可启停/重载。
编写规范见 [docs/PLUGIN-DEVELOPMENT.md](docs/PLUGIN-DEVELOPMENT.md)；
完整示例见 `plugins/currency.py`，详细设计见 `docs/PLUGIN-SYSTEM-V2.md`。

## 架构原则

1. **所有能力都是 MCP 工具**：不引入专用 API，统一通过工具层暴露给 LLM
2. **LLM 自动决定**：不硬编码触发条件，让 LLM 在对话上下文中自行判断何时调用工具
3. **原生 Function Calling**：工具以 OpenAI `tools` schema 注入，模型原生返回 `tool_calls`（比文本协议可靠）
4. **user 消息永远最后**：所有系统注入都在 user 之前，避免指令盖过用户请求
5. **上下文兜底**：模型偶发传空参数时，用最后一条用户消息自动填充必填参数
6. **单用户不变**：`_UID = "assistant-main"`，所有工具共享同一个用户上下文
7. **安全沙箱**：文件操作限 WORK_DIR、代码执行禁危险模块、命令黑名单
