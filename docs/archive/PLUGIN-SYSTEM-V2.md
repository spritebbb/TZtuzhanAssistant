# 菟菚插件系统 v2 升级方案

> 目标：修复现有插件卸载/重载残留 bug，补齐管理能力（元信息 / API / 前端界面），
> 并把插件能力从「只能注册工具」扩展为可注册定时任务、系统提示注入、HTTP 路由、消息钩子。

## 一、现状与动因

- 现有插件系统（v1，路线图 v9）：`plugins/*.py` 暴露 `register()`，
  内部 `ToolRegistry.register_func` 注册工具；启动自动加载 + 2s 轮询热加载。
- 已知 bug（CODE-REVIEW #15）：加载插件时用 `after - before` 差集清点该插件注册的工具，
  **插件覆盖内置同名工具时差集清点不到** → 卸载/重载后被覆盖的工具状态不一致（残留/丢失）。
- 无管理入口：看不到已装插件、不能启停/禁用/重载，只能看日志。
- 能力单一：只能注册工具。

## 二、分层设计

### P0 修复卸载/重载残留（`backend/tools/base.py` + `backend/plugins/loader.py`）

1. `FunctionTool` 增加 `owner` 字段（默认 `"builtin"`），loader 加载后把该插件注册的工具标 owner=插件名。
2. `load_plugin()`：加载前快照**全部**已注册工具（name → FunctionTool 对象引用），
   加载后对比：
   - 新出现的名字 → 该插件的 `tools`；
   - 名字相同但对象引用变了 → 记入 `overridden`（name → 旧工具对象）。
3. `unload_plugin()`：先 `unregister(tools)`，再按 `overridden` **恢复快照里的旧工具**
   （有备份恢复备份，无备份移除）。彻底替代 `register_all()` 全量重注册的兜底方案。

### P1 插件元信息（向后兼容）

插件文件可选暴露模块级 `PLUGIN_META`：

```python
PLUGIN_META = {
    "name": "汇率换算",       # 展示名（默认用文件名）
    "version": "1.0.0",
    "description": "USD→CNY 汇率换算",
    "author": "you",
}
```

- 无 `PLUGIN_META` 的旧插件照常工作（register() 兼容无参/带参两种签名）。
- `PluginState` 记录 meta，供 API/前端展示。

### P2 管理 API（新增 `backend/api/plugins.py`）

| 接口 | 作用 |
|---|---|
| `GET /api/plugins` | 列表：名称/版本/描述/作者/状态（loaded·failed·disabled）/注册工具/HTTP 路由/定时任务/错误信息/文件时间 |
| `POST /api/plugins/{name}/enable` | 从禁用集合移除并立即加载 |
| `POST /api/plugins/{name}/disable` | 卸载（含路由/任务/钩子清理）并加入禁用集合 |
| `POST /api/plugins/{name}/reload` | 手动重载（禁用状态返回 400） |

- 禁用集合持久化到 `data/plugins.json`，重启保留；
  `load_all_plugins()` 与热加载循环均跳过禁用插件。
- loader 提供 `plugin_states()` 供 API 读取状态快照（含加载失败的 error）。

### P3 前端管理界面（`SettingsPanel.vue` 新增「插件」区块）

- 插件卡片：展示名 + 版本 + 描述 + 状态徽章（绿=已加载 / 红=失败 / 灰=禁用）、
  注册的工具与路由清单、失败时显示错误详情；
- 操作按钮：启用 / 禁用 / 重载；沿用藤蔓主题样式（仿 MCP 区块）。

### P4 插件能力扩展（新规范：`def register(ctx)`，ctx 为 PluginContext）

- **a. 定时任务**：`ctx.schedule(interval_seconds, fn, name="")` → 周期后台任务，
  插件卸载/重载时自动取消。要求事件循环运行中（启动加载与热加载均在 async 上下文内）。
- **b. 系统提示钩子**：`ctx.on_system_prompt(fn)`，fn() -> str|None；
  在 `persona.build_system_prompt` 末尾追加各插件贡献的文本（异常跳过并记日志）。
- **c. HTTP 路由**：`ctx.route(method, path, handler)`（FastAPI 风格 handler，收 `Request` 返回响应），
  统一挂到**插件网关** `/plugins/{plugin}/{path}`（app 启动时注册一个网关路由，
  内部按当前插件的路由表动态分发）→ 支持热装卸，无需重启。
- **d. 消息钩子**：
  - `ctx.on_user_message(fn)`：fn(user_text) -> str|None，在 `pipeline._process_locked` 入口
    应用于用户消息（改写后文本用于全流程）；
  - `ctx.on_reply(fn)`：fn(reply) -> str|None，在最终回复存档/返回前应用。
  - 两者均逐个调用、异常跳过并记日志，永不阻断主流程。

### 明确不做（本轮排除）

- 插件自定义前端 UI 卡片（需前端动态组件体系，单独立项）
- 插件市场 / 远程下载（安全面大，无远程源）
- 插件间依赖声明（当前无实际需求）

## 三、实施顺序

1. P0 快照恢复 + 回归测试
2. P1 PLUGIN_META + loader v2（ctx 注入）
3. P2 管理 API + 禁用持久化 + 热加载整合
4. P4 能力扩展（context.py：PluginContext / 调度 / 钩子注册表 / 网关）+ pipeline / persona 接线
5. currency.py 示例升级到新规范
6. P3 前端「插件」区块
7. `tests/test_plugins.py`（快照恢复回归 / API 启停 / 禁用持久化 / 路由网关 / 定时任务清理）
8. HARNESS_ROADMAP.md 更新

## 四、验证标准

- `tests/test_plugins.py` 全绿（含 P0 bug 专项回归）
- 项目现有 pytest 全量回归不破
- 启动后端实测：`GET /api/plugins` 列表、启停/重载、网关路由可达、热加载与禁用共存

## 五、后续迭代：工具全面插件化（2026-09-02）

在保留**人格系统、记忆系统、好感度**三大核心的前提下，其余 11 个工具模块全部
从 `backend/tools/builtin/` 迁移为 `plugins/` 目录的标准插件，统一走插件系统管理
（设置面板可启停/重载/禁用，热加载生效）：

| 插件 | 工具 |
|---|---|
| web_search | web_search |
| web_fetch | web_fetch |
| file_ops | read_file / write_file / list_dir |
| file_search | glob / grep |
| file_edit | edit |
| todo | todo_create / todo_list / todo_get / todo_update / todo_complete / todo_delete |
| subagent | agent_run / agent_fanout |
| skill | skill_search / skill_load |
| code_exec | run_python / run_command |
| system | system_info / 进程 / 窗口 / 截图 / 剪贴板 / 打开应用（10 个） |
| external | codex_run / dsh_run |
| currency | currency_convert（示例插件） |

- `backend/tools/builtin/register_all.py` 收敛为**仅注册 memory**（记忆工具
  memory_search / memory_add 保留内置，不参与插件启停）；
- 迁移要点：工具模块相互零依赖（引用分析确认），相对导入改绝对导入
  （`backend.tools.base` / `backend.tools.safety` / `backend.core.*` / `backend.skills.*`），
  `register()` → `register(ctx)`，每个工具带 `owner=<插件名>`；
- 已知边界：`ctx.schedule` 需要事件循环（后端 startup / 热加载 / 管理 API 均为
  async 上下文；同步脚本加载含定时任务的插件需 `asyncio.run` 包裹，报错信息已明确提示）；
- 测试：新增 `tests/test_pluginized_tools.py`（12 插件 / 33 工具 / owner / 元信息全量校验），
  `tests/_helpers.py` 提供 `load_all_tools()`（= 后端 startup 等价装载），受影响的
  测试文件已切换到该 helper。
