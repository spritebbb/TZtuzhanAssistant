# 菟菚桌面助手插件编写规范

> 适用范围：插件系统 v2（`backend/plugins/loader.py` + `backend/plugins/context.py`），
> 即项目根 `plugins/*.py` 的 Python 插件。所有工具模块已插件化（记忆工具 memory_* 保留内置）。
>
> 相关材料：
> - 框架实现：`backend/plugins/loader.py`、`backend/plugins/context.py`
> - 工具基类/注册表：`backend/tools/base.py`
> - 安全校验：`backend/tools/safety.py`
> - 设计文档：`docs/PLUGIN-SYSTEM-V2.md`
> - 完整示例：`plugins/currency.py`（汇率换算，覆盖全部 ctx 能力）
> - 管理 API：`backend/api/plugins.py`

---

## 1. 插件是什么

一个插件 = `plugins/` 目录下的**一个 `.py` 文件**。文件暴露 `register()` 函数，
通过 `ToolRegistry` / `PluginContext(ctx)` 向系统注册能力。

启动时后端自动加载全部插件，之后每 2 秒轮询一次 `plugins/` 目录：
新增/修改/删除 `.py` 文件都会自动加载 / 重载 / 卸载，无需重启服务。
设置面板「插件」区和 `backend/api/plugins.py` 提供启停、重载、查看状态与源码。

### 1.1 可注册的能力

| 能力 | 注册入口 | 说明 |
|---|---|---|
| 工具 | `ToolRegistry.register_func(...)` | LLM 原生 Function Calling 调用，最核心的能力 |
| 定时任务 | `ctx.schedule(interval, fn, name="")` | 周期后台任务，卸载/重载自动取消 |
| 系统提示注入 | `ctx.on_system_prompt(fn)` | 追加到系统提示末尾，`fn() -> str \| None` |
| HTTP 路由 | `ctx.route(method, path, handler)` | 挂到 `/plugins/{插件名}/{path}` 网关 |
| 用户消息钩子 | `ctx.on_user_message(fn)` | 改写用户消息，`fn(text) -> str \| None` |
| 回复钩子 | `ctx.on_reply(fn)` | 改写最终回复，`fn(reply) -> str \| None` |

### 1.2 框架负责什么

- 启动加载、2 秒热加载（新增/修改/删除）、失败自动回滚与重试；
- 插件禁用状态持久化（`data/plugins.json`），重启后保持；
- 卸载/重载时自动清理：插件注册的工具、ctx 定时任务、钩子、路由；
- 插件覆盖内置/其他插件工具时，卸载后按加载前快照**精确恢复**，不残留也不丢工具；
- 工具执行统一走确认钩子 + 审计日志。

### 1.3 框架不负责什么

- **不会**清理插件自己创建的线程、直接 `asyncio.create_task` 的任务、
  子进程、打开的文件句柄/连接等资源——这些必须由插件自己管理；
- **不会**隔离插件权限：插件运行在助手进程内，拥有本机代码执行权限；
- **不会**做沙箱：插件与后端共享内存与数据目录，请按第 12 节安全要求编写。

---

## 2. 文件与命名

### 2.1 硬性约束

- 插件放在项目根 `plugins/`，**单文件**，不支持子目录、不支持包（`__init__.py`）；
- 文件名即插件标识（模块名），必须为 `[A-Za-z0-9_-]`，且**不得以下划线开头**
  （`_xxx.py` 被当作辅助文件跳过，不会被加载）；
- 文件编码 UTF-8；建议首行保留 `# -*- coding: utf-8 -*-`；
- 一个文件只放一个插件；不要在 `plugins/` 里放与插件无关的脚本。

### 2.2 命名约定

| 对象 | 约定 | 示例 |
|---|---|---|
| 文件名（插件 id） | 英文小写 snake_case | `weather.py` |
| `PLUGIN_META["name"]` | 中文展示名，可与文件名不同 | `天气助手` |
| 工具名 | 英文小写 snake_case，简短且描述行为 | `weather_query` |
| 定时任务名（`ctx.schedule` 的 name） | snake_case，具备语义 | `cache-refresh` |
| HTTP 路径 | 小写，不带前后缀斜杠 | `ctx.route("GET", "/status", ...)` |

> 改名注意：文件名是插件身份。重命名文件 = 旧的卸载 + 新的加载，
> 之前注册的工具会被撤销并恢复原状，请保持文件名稳定。

---

## 3. 最小骨架与元信息

```python
# -*- coding: utf-8 -*-
"""示例插件：问候增强。"""
from __future__ import annotations

from backend.tools.base import ToolRegistry

PLUGIN_META = {
    "name": "问候增强",
    "version": "1.0.0",
    "description": "演示最小插件的写法：注册一个 hello 工具",
    "author": "you",
}


def _hello(name: str = "") -> str:
    """问候工具实现。"""
    return f"（你好，{name or '世界'}！我是插件工具 hello。）"


def register(ctx) -> None:
    ToolRegistry.register_func(
        name="hello",
        description="向用户打招呼。用户说『你好』『打招呼』等时使用",
        func=_hello,
        owner="hello_plugin",   # 建议显式标为当前插件名（loader 也会自动补标）
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要问候的人名（可选）"},
            },
        },
    )
```

### 3.1 PLUGIN_META 字段

可选，供管理 API / 设置面板展示；缺失时展示名回落到文件名。

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | str | 展示名（中文），默认取文件名 |
| `version` | str | 版本号，建议语义化版本如 `1.2.0` |
| `description` | str | 一句话说明插件与工具用途 |
| `author` | str | 作者 |

`PLUGIN_META` 必须是模块级常量字典；不要在加载后动态改写。

---

## 4. register(ctx)

```python
def register(ctx) -> None:  # 推荐签名（ctx 可选用）
    ...
```

规则：

- **必须**暴露可调用的 `register`，否则插件加载失败；
- 推荐 `def register(ctx) -> None`。旧插件 `def register()`（无参）仍兼容，
  loader 会按签名自动判断是否传 ctx；也允许 `def register(ctx=None)`；
- register 在每次加载时执行一次，必须**声明式、快速、无副作用**：
  只做注册与轻量常量初始化，不做网络请求、不读写大文件、不启动线程；
- 必须**幂等/可重入**：热加载 = 卸载旧模块 + 重新 import + 重新执行 register，
  不要在 register 里依赖“进程生命周期内只跑一次”的假设；
- 不要把实现逻辑写在 register 里，工具实现拆成模块级函数再注册。

---

## 5. 注册工具：ToolRegistry.register_func

工具是插件最主要的形态：LLM 通过原生 Function Calling 看到工具的
`name / description / input_schema`，自行决定何时调用。

### 5.1 参数说明

| 参数 | 默认 | 说明 |
|---|---|---|
| `name` | — | 工具名，全局唯一（见 5.5） |
| `description` | — | 给 LLM 看的说明：**何时使用 + 做什么**，写清楚触发场景 |
| `func` | — | 工具函数，async/sync 均可（见 5.2） |
| `input_schema` | `{}` | JSON Schema，与 func 的参数一一对应（见 5.3） |
| `is_async` | `True` | `False` 表示 sync，会放进线程池执行 |
| `category` | `"read"` | `read` / `write` / `run` / `external`，影响确认策略 |
| `danger_level` | `"normal"` | `info` / `normal` / `high` / `critical`，影响确认文案与策略 |
| `needs_confirm` | `False` | `True` 强制要求用户确认 |
| `max_output_chars` | `4000` | 结果截断上限（0 = 不截断） |
| `owner` | `"builtin"` | 建议显式填当前插件文件名；loader 也会为新增/覆盖工具自动补标 |

### 5.2 工具函数设计

```python
async def _my_tool(query: str = "", limit: int = 5) -> str:
    """工具函数的 docstring：给人/模型看的一句话说明。"""
    ...
    return "（正常结果用自然语言/结构化文本返回）"
```

- 函数参数名与 `input_schema.properties` 的键一致；必填项放进 `required`，
  可选参数给默认值（`func(**args)` 只会传入调用方给的键）；
- 函数可以 `async def`，也可以普通函数配合 `is_async=False`
  （后者在线程池执行，不阻塞事件循环；阻塞式网络/IO 请用它或 `asyncio.to_thread`）；
- **返回字符串**（或可 `str()` 的对象）。结果默认超 4000 字符自动保留头尾截断，
  长输出请在函数内自行压缩，并主动给 LLM 简洁可读的文本；
- 可预期的失败**不要抛异常**：返回 `（原因）` 风格的中文说明，
  例如 `"（文件不存在: xxx）"`、`"（汇率获取失败，请稍后再试）"`；
- 真正的编程缺陷才让它抛出——引擎会把异常转成 `类型: 消息` 的失败结果并审计，
  不会让进程崩掉；
- 不做参数绑定之外的假设：缺参/未知参数会被引擎判为“参数错误”返回给模型。

### 5.3 description 与 input_schema 的写法

`description` 决定模型会不会用、何时用，必须包含**触发场景**：

```python
description="查询指定城市的实时天气。用户提到天气、气温、降雨、出门要不要带伞时使用",
input_schema={
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "城市名，如 上海"},
        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "description": "温度单位"},
    },
    "required": ["city"],
},
```

### 5.4 category / danger_level 语义（安全关键）

| category | 含义 | 确认策略 |
|---|---|---|
| `read`（默认） | 只读，无副作用 | 自动执行 |
| `write` | 写操作（文件、数据、状态变更） | 需要用户确认 |
| `run` | 命令 / 进程执行 | 需要用户确认 |
| `external` | 外部 Agent / 远程执行 | 需要用户确认 |

| danger_level | 含义 |
|---|---|
| `info` | 无风险 |
| `normal`（默认） | 常规操作 |
| `high` | 高风险（删除/覆盖/命令/外部执行），红色确认文案 |
| `critical` | 直接拒绝，不弹确认（引擎硬拦截） |

规则：

- 写操作、命令、外部调用**必须**标对 category（或 `needs_confirm=True`），
  确认钩子缺失时不要指望它替你做安全决策；
- 明显不可逆/高危的操作把 `danger_level="high"`；
- 需要“策略上永远不允许”的操作用 `"critical"`；
- `needs_confirm=True` 可以让 read 类也弹确认（谨慎使用）。

### 5.5 工具名唯一性与覆盖

- 注册表按工具名做 key，同名注册会**覆盖**（插件可覆盖内置工具，插件优先级更高）；
- **尽量避免覆盖内置或他人插件的同名工具**；确需覆盖时，卸载会自动按快照恢复，
  但多插件同名会产生加载顺序耦合，难排查；
- 同一插件内的工具名不要重复注册；重复注册以最后一次为准。

---

## 6. 定时任务：ctx.schedule

```python
def _refresh_cache() -> None:
    ...

def register(ctx) -> None:
    ctx.schedule(600, _refresh_cache, name="rate-refresh")   # 每 600 秒
```

- `interval` 为秒数，必须 > 0；
- `fn` 支持同步与 async；每轮执行异常会被捕获并记日志，**任务不会中断**；
- 返回任务名；未给 name 时自动生成 `{插件名}:job{n}`，建议显式命名以便在
  `GET /api/plugins` 的任务清单里辨识；
- 任务由插件卸载/重载自动取消；
- 需要事件循环：后端启动、热加载、管理 API 均为 async 上下文，满足条件；
  同步脚本里加载含 schedule 的插件会报“需要事件循环”，请用 `asyncio.run` 包裹；
- 不要在任务里做无限循环或超重计算；长任务自行设置超时/并发上限。

---

## 7. 系统提示注入：ctx.on_system_prompt

```python
ctx.on_system_prompt(lambda: "- 你带有汇率换算工具，用户问美元/汇率时直接调用。")
```

- `fn()` 返回 `str | None`；非空字符串会被拼接到系统提示**末尾**（每条换行分隔）；
- 工具 schema 本身已经告诉模型“有什么工具”，这里**只补充行为策略**
  （何时必须调用、如何汇报），不要整段复述工具描述；
- 内容保持简短、稳定；输出太长会挤占上下文。

---

## 8. HTTP 路由：ctx.route

```python
async def _status(request):
    return {"ok": True, "plugin": "currency", "cached": 7.24}

def register(ctx) -> None:
    ctx.route("GET", "/status", _status)      # 最终 URL: /plugins/currency/status
```

- `method` 支持 `GET/POST/PUT/DELETE/PATCH`（自动转大写）；
- `path` 不含插件前缀，首尾斜杠自动归一；支持多级，如 `"/sub/status"`；
- 最终地址：`/plugins/{插件文件名}/{path}`（例如 `/plugins/currency/status`），
  由 `backend/api/plugins.py` 的网关统一分发，插件热装卸即时生效；
- handler 收 FastAPI `Request`，可为 sync/async，返回
  `Response` / `dict` / `list` / `str`（dict/list 自动转 JSON，str 包一层 `{ok, data}`）；
- 网关未命中返回 404，handler 抛异常返回 500，均带日志；
- **不要在 handler 里信任前端输入**：校验参数、限制长度、不暴露内部文件/环境变量；
- 同一 (method, path) 重复注册会覆盖；卸载/重载自动移除。

---

## 9. 消息钩子：on_user_message / on_reply

```python
def _append_tag(text: str) -> str | None:
    return text + "（来自插件演示）"

def register(ctx) -> None:
    ctx.on_user_message(_append_tag)   # 用户消息进入 pipeline 前调用
    ctx.on_reply(_append_tag)          # 最终回复存档/返回前调用
```

- 钩子链按“插件加载顺序 → 插件内注册顺序”逐个执行；
- 钩子返回非空 `str` 时**替换**当前文本并传给下一个钩子；返回 `None`/其他则保持不变；
- 钩子异常一律被捕获、记日志、跳过，**永不阻断主流程**；
- 钩子要快、要幂等，不要在里面做网络请求或重 IO（每轮对话都会跑）；
- 谨慎改写用户消息：结果会影响整轮对话的语义；如无必要请用 `on_reply` 或干脆不用钩子。

---

## 10. 生命周期与清理语义

### 10.1 加载流程

1. 后端启动：先注册内置工具（当前仅 memory_*），再按文件名排序加载
   `plugins/*.py`（loader 会先对注册表做快照）；
2. 执行模块 import（模块级代码）→ 执行 `register()`；
3. 对比快照，把插件新增/覆盖的工具打上 `owner=<插件名>`，记录 tools /
   routes / tasks / hooks 清单到 `PluginState`；
4. 启动 2 秒热加载循环：新增→加载、修改→重载、删除→卸载；加载失败的插件
   记录 error 并在下轮自动重试。

### 10.2 卸载/重载清理

卸载或重载时自动完成：

- 移除该插件注册的工具（按对象归属判断，避免误删后来者的同名工具）；
- 恢复被该插件覆盖的旧工具（快照恢复，CODE-REVIEW #15 的回归保证）；
- 取消 ctx.schedule 注册的定时任务；
- 移除该插件的全部钩子与 HTTP 路由；
- 从 `sys.modules` 移除旧模块（下次 import 得到全新模块对象）。

### 10.3 插件自己负责的收尾

以下**不会**被框架清理，请勿在插件里随意创建：

- 裸 `asyncio.create_task` / `threading.Thread` / `multiprocessing` 常驻任务；
- 常驻子进程、打开的文件、数据库连接、网络连接；
- 写进全局作用域之外的共享状态（如往别的模块塞全局引用）。

若确实需要，插件必须自行提供清理途径（如监听卸载/重载前状态重置），
否则重载后可能残留孤儿任务。常规后台周期性工作请用 `ctx.schedule`。

### 10.4 禁用与状态

- 禁用会**立即卸载**并写入 `data/plugins.json`，重启后保持禁用；
- 管理入口：`POST /api/plugins/{name}/enable|disable|reload`，
  `GET /api/plugins` 列表，`GET /api/plugins/{name}/source` 查看源码；
- 设置面板「插件」区块提供同等的可视化操作；
- 状态字段：`loaded / disabled / error / tools / routes / tasks / hooks /
  display_name / version / description / author / mtime`。

---

## 11. 编码规范

### 11.1 必须

- 文件头 `# -*- coding: utf-8 -*-` + 模块 docstring（一句话说明插件用途）；
- 顶部 `from __future__ import annotations`；
- **绝对导入**：插件以顶层模块方式加载（`plugin_{文件名}`），内部一律写
  `backend.xxx.yyy` 绝对导入，禁止相对导入（`from ..tools.base import ...` 会失败）；
- 复用框架设施：工具注册走 `backend.tools.base.ToolRegistry`；路径/命令校验走
  `backend.tools.safety.check_path / check_command / check_cwd`；配置走
  `backend.core.config.config`；日志走 `backend.core.log.logger`；
- 函数与工具参数写类型注解，docstring 说明行为；
- 模块级可写状态集中放一处并注释用途（如汇率缓存示例），方便审查与重载排查；
- 对外部依赖保持克制：优先标准库，确需第三方库时加入 `requirements.txt` 并说明。

### 11.2 禁止

- 在 import 阶段产生副作用：顶层网络请求、文件写入、启动线程、执行 register；
- 在 register() 里做耗时/阻塞/IO 操作；
- 用 `print` 直接向 stdout 输出（工具结果用 return 返回，日志用 logger）；
- 相对导入；写死本机个人路径（如 `D:\DSH\...`）；把密钥写进插件源码；
- 不经确认执行写操作、命令、外部调用；
- 在工具/钩子/路由里抛出未捕获异常作为“正常错误路径”（可用返回文本表达）；
- 修改 `backend/` 框架代码来满足插件需求——框架能力不足请先提需求。

---

## 12. 安全检查清单（提交前逐条核对）

**通用**

- [ ] 只做声明式注册，无 import 副作用；
- [ ] 不读写 `.env`、不打印/返回密钥；
- [ ] 工具/钩子/路由对输入做长度与类型校验，不信任模型或前端参数。

**文件类**

- [ ] 路径先 resolve 再经 `safety.check_path` 白名单校验（参考 `plugins/file_ops.py`）；
- [ ] 拒绝 `..`、绝对越界路径，错误信息明确。

**命令/代码类**

- [ ] 命令先经 `safety.check_command` + `check_cwd`（参考 `plugins/code_exec.py`）；
- [ ] 用 `asyncio.create_subprocess_exec`（列表参数），**不用** `shell=True`；
- [ ] 全部子进程设超时，超时显式 kill 进程树（Windows 用 `taskkill /T /F`）；
- [ ] 标注真实风险等级：等同本机权限的操作在 description 里明示，确认弹窗让用户知情。

**网络类**

- [ ] 出网请求带超时（如 `urlopen(timeout=...)` / `asyncio.wait_for`）；
- [ ] 响应体与返回文本限制大小；外部数据当作不可信文本处理；
- [ ] 接口失败返回可读错误，不把裸异常栈塞给模型。

**状态与资源**

- [ ] 需要跨重载保留的数据存到磁盘（`data/`）或后端持久层，不依赖模块对象存活；
- [ ] 不创建框架无法回收的线程/裸任务/连接；
- [ ] 定时任务体量小、异常可自愈。

**注册表**

- [ ] category / danger_level / needs_confirm 与真实风险一致；
- [ ] 工具名不与内置及其他插件冲突（确需覆盖要有意为之并注释原因）；
- [ ] `owner` 显式填插件名。

---

## 13. 自测与交付流程

1. 新建/修改 `plugins/{name}.py` 后保存，观察后端日志：
   - `[热加载] 发现新插件/插件变化，重载: xxx.py`
   - `[热加载] 已加载/重载完成`
2. 查看状态：`GET /api/plugins`，确认 `loaded: true`、tools/routes/tasks 清单正确；
3. 查看源码接口：`GET /api/plugins/{name}/source`（越权/路径穿越会 400/404）；
4. 触发一次真实对话或手工调用工具，确认 description 能驱动 LLM 正确调用；
5. 重载/禁用一次，确认旧工具被移除、被覆盖的工具恢复、钩子/任务/路由消失；
6. 写单测并跑回归：

```powershell
python tests/test_plugins.py            # 插件系统 v2 回归（快照恢复/API/钩子/任务）
python -m pytest tests/test_pluginized_tools.py tests/test_plugins.py
```

7. 交付时在 `PLUGIN_META` 里给出版本与说明；涉及行为的改动同步更新
   `HARNESS_ROADMAP.md` 的“插件开发规范”或本文件。

---

## 14. v1 → v2 迁移

旧插件（v1，只注册工具、无 ctx、无 META）仍可直接加载；需要新能力时按以下步骤升级：

1. 顶部补充 `PLUGIN_META`；
2. 函数签名 `def register():` → `def register(ctx):`（或保留无参兼容）；
3. 把相对导入改成 `backend.*` 绝对导入；
4. `ToolRegistry.register_func(...)` 补充 `owner="<文件名>"`；
5. 按需调用 `ctx.schedule / on_system_prompt / route / on_user_message / on_reply`；
6. 删掉旧版手工 `register_all()` 条目（若曾在其中登记），改由插件系统统一加载；
7. 跑 `tests/_helpers.py` 提供的 `load_all_tools()` 等价的启动装载做全量回归。

---

## 15. 完整示例模板

```python
# -*- coding: utf-8 -*-
"""示例插件：时钟播报（演示 v2 全部能力：工具/定时任务/路由/钩子）。"""
from __future__ import annotations

import datetime

from backend.tools.base import ToolRegistry

PLUGIN_META = {
    "name": "时钟播报",
    "version": "1.0.0",
    "description": "查询当前时间；每小时自动刷新；演示插件开发模板",
    "author": "you",
}

# 模块级缓存：重载后随旧模块释放（磁盘之外的数据请自行持久化）
_cached_time = {"text": "", "ts": 0.0}


def _now_text() -> str:
    now = datetime.datetime.now()
    text = now.strftime("%Y-%m-%d %H:%M:%S")
    _cached_time.update(text=text, ts=now.timestamp())
    return text


async def _current_time() -> str:
    """查询当前本地时间。用户问现在几点、当前时间、日期时使用。"""
    return f"（现在是 {_now_text()}）"


def _refresh_job() -> None:
    _now_text()  # 每 30 分钟预热一次缓存


async def _status(request) -> dict:
    """管理路由：GET /plugins/clock/status 查看插件状态。"""
    return {"ok": True, "plugin": "clock", "cached": _cached_time}


def _clock_hint(reply: str) -> str:
    """演示 on_reply：只在用户明显问时间时兜底补充（生产中请谨慎使用钩子）。"""
    if "几点了" in reply or "时间" in reply and "现在是" not in reply:
        return reply + f"\n（附：当前 {_now_text()}）"
    return reply


def register(ctx) -> None:
    # 1) 工具
    ToolRegistry.register_func(
        name="current_time",
        description="查询当前本地日期与时间（秒级精度）",
        func=_current_time,
        owner="clock",
        input_schema={"type": "object", "properties": {}},
    )
    # 2) 定时任务
    ctx.schedule(1800, _refresh_job, name="time-refresh")
    # 3) HTTP 路由
    ctx.route("GET", "/status", _status)
    # 4) 回复钩子（示例）
    ctx.on_reply(_clock_hint)
    # 5) 系统提示（告诉模型何时该调用）
    ctx.on_system_prompt(lambda: "- 你带时钟工具 current_time，用户问时间时直接调用，不要猜。")
```

---

## 16. 常见问题

**保存文件后没生效？**
热加载轮询间隔 2 秒；确认插件未被禁用（`GET /api/plugins` 里 `disabled`），
文件没以 `_` 开头，且后端日志没有 `加载失败`。失败状态里能看到 `error` 详情，
修改后再次保存即自动重试，也可用 `POST /api/plugins/{name}/reload` 手动触发。

**提示“ctx.schedule 需要事件循环”？**
该插件在同步上下文被加载（如同步测试脚本）。后端启动/热加载/管理 API 都满足，
同步脚本请用 `asyncio.run(...)` 包住 `load_all_plugins()`。

**卸载/重载后模块级状态还在吗？**
卸载会弹出 `sys.modules`，重载得到全新模块对象：模块级变量相当于被重置。
若这些状态被外部引用（线程闭包等）则旧对象不会被回收——这正是要避免裸线程的原因。

**插件加载失败会污染注册表吗？**
不会。loader 对失败做快照回滚：半路注册/覆盖的工具全部恢复，ctx 能力一并清理，
并把 `error` 写入状态供管理界面展示。

**想覆盖内置工具？**
可以，且卸载时会按快照精确恢复。请先确认你的工具在语义与安全级别上确实
取代内置版本，并在注释里说明原因。
