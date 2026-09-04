# 修复报告 V13 —— backlog 修复 + 智能主动归档

> 本轮：从「只审查」切换到「修复模式」，修复累计 backlog，并新增菟菚智能主动归档能力。
> 全部改动均通过 `py_compile` 编译 + 项目测试 + 前端 vue-tsc 类型检查，未破坏任何既有功能。

---

## 一、修复的 Bug（6 项 backlog + 1 项说明）

| 编号 | 级别 | 位置 | 问题 | 修复 |
|------|------|------|------|------|
| B3 | 🟡 | `core/daily.py:128` | `get_user()` 未判空，异常被后台任务静默吞掉 | 加 `u = get_user(); if u is None: return` |
| B4 | 🟡 | `core/affection.py` `_scale_delta` | 雀跃时 `round(-0.5)=0` 吞掉轻微扣分 | 负向变动加 `min(delta, scaled)` 保底 |
| B5 | 🟡 | `core/config.py` `update_env_file` | 密钥值含 `#`/空格被 dotenv 当注释截断 | 含特殊字符时双引号包裹+转义 |
| B6 | 🟡 | `core/greeting.py` | 读-判-写被 `await` 打断致并发重复问候 | 加 kv 占位标记，`finally` 释放 |
| B2 | 🟡 | `plugins/code_exec.py` | 超时 kill 只杀主进程，孙进程残留 | 新增 `_kill_tree()`（taskkill /T） |
| B7 | 💭 | `core/pipeline.py` `_TOOL_LOOP_KEYS` | 裸单字 `"查"` 误配无关词 | 删除裸 `"查"`，保留明确短语 |
| B8 | 💭 | `plugins/external.py` | `D:\DSH` 硬编码路径 | 环境变量优先 + 相对推导 |
| B1 | 🟡 | `userdb.update_task` | f-string 拼 SQL | 字段名已由白名单约束，仅加注释（本就安全） |

---

## 二、修复要点

### B4 扣分保底（关键正确性修复）

实测验证：

```
mult=1.5（雀跃）delta=-1 → -1   ← 修复前被吞成 0
mult=1.5（雀跃）delta=-3 → -3
mult=0.6（低落）delta=-5 → -7   ← 心情差仍加重扣分
```

修复后：心情好时轻微扣分不再被吞（保底 `min(delta, scaled)`），心情差时仍按 `×1.4` 加重，完全符合"心情好扣分轻、心情差扣分狠"的设计意图。

### B6 问候并发去重

`greeting_for` 的锁只保护了「读-判」阶段，`_greeting_text` 在锁外 `await`，两个并发请求可能都判定"久别"后重复问候。修复：在锁内先占位 `_GREET_PENDING_KEY`，并发请求见占位即跳过；`finally` 里 `kv_del` 释放，避免一次失败永久卡死问候。

### B2 孙进程清理

`run_python`/`run_command` 超时 `proc.kill()` 只结束主进程，若代码 spawn 子进程会残留孤儿。新增 `_kill_tree()`：Windows 用 `taskkill /PID <pid> /T /F` 连同进程树一起结束，其他平台回退 `proc.kill()`。

---

## 三、新增功能：菟菚智能主动归档

**设计：建议式（不擅自清空）**——擅自归档可能让用户丢失当前对话上下文，故菟菚只在会话过长时**提醒**用户归档，由用户决定是否执行。

### 后端

1. `session/store.py` 新增 `message_count()`：查当前会话消息数。
2. `core/initiative.py` 新增 `maybe_suggest_archive()`：
   - 当前会话消息数 ≥ 40（`_ARCHIVE_THRESHOLD`）
   - 当天未提醒过（kv 去重）
   - 生成一条菟菚风格的归档建议，`enqueue_proactive` 投递（走 SSE 秒级推送）
   - 挂进 `_tick_once`，不受全局 15min cooldown 影响，随后台循环每 5min 检查

### 前端

1. `ChatView.vue`：消息数 ≥40 时对话上方显示归档提示条（"这段对话已经挺长了，要不要先归档存档？"），带「归档当前对话」按钮 + 关闭按钮。
2. `App.vue`：接 `@request-archive` 事件到已有 `archiveNow()`，复用完整归档逻辑（loading 态 / 刷新侧栏归档列表 / 清空对话区）。

> 手动归档入口（header 右上角归档图标）在 V11 已加，本轮保留并新增对话内提示条，两处入口并存。

---

## 四、验证结果

| 验证项 | 结果 |
|--------|------|
| `py_compile`（8 个改动文件） | ✅ 全部通过 |
| `test_tool_loop_trigger.py`（B7） | ✅ 9 条命中 / 4 条不误伤 |
| `test_pipeline_scenario.py` | ✅ 7 通过 0 失败 |
| `test_plugins.py`（插件系统 v2） | ✅ 全部通过 |
| `test_pluginized_tools.py`（35 工具） | ✅ 全部通过 |
| `test_p5_external.py`（B8 外部桥） | ✅ 4/4 通过 |
| 前端 `vue-tsc --noEmit` | ✅ 无错误 |

---

## 五、剩余 backlog（未动，可选）

| 编号 | 位置 | 问题 | 状态 |
|------|------|------|------|
| B9 | `tools/builtin/memory.py` | 导入私有函数 `_recall_with_expansion` | 已核实非 bug（`__init__.py` 有导出） |
| B10 | `plugins/currency.py` | 示例插件同步阻塞网络 | 示例性质，可接受 |

至此，前 5 轮审查累计发现的全部 P2/P3 级问题已修复或确认非问题。P0/P1 始终为 0（历史 P1 已在 V9/V10/V11 轮修复）。
