# 迭代报告：M1 收尾 + M8 双切片（2026-09-06）

> 执行：ZCode（GLM）headless 会话实现初稿 + 最终全量验收；Codex（GPT-5）独立审查与修补（后额度耗尽，验收由 ZCode 接手完成）
> 双代理流水线首批实战：zcode_exec 派发（edit 模式落盘）→ 信箱交接 → 独立审查 → 全量验收。

## 本批交付

### M1 收尾 · 临时对话 + 事实自然衰减
- 「本轮不留痕」开关 + 自然语言识别（privacy.py 正则，插件钩子前隔离全部持久副作用）。
- 事实提炼产出 retention_days（1–365）落为 expires_at；到期 active/unpinned 事实经 fact_lifecycle 权威路径级联删除；daily batch 空白日/重复触发幂等兜底。
- MemoryPanel「长期保留」固定入口；pinned 永不自动衰减。

### M8.1 · 写给未来的我们
- 日期/目标/事件三类确定性解锁；sealed/ready 响应完全不含 body 键；显式拆信幂等建 artifact；真删除级联。
- schema v10（future_letters）；Codex 修补：时区输入 400 边界、feature flag 前端降级、真实 API→DOM 密封态 E2E。

### M8.2 · 30/100/365 天关系快照
- 确定性汇编真实持久数据（events/artifacts/diary/terms/viewpoints），cutoff 定格资格，来源 manifest + 显式截断计数；冻结文档语义（POST 不重算，删除真删后可重建）。
- schema v11（relationship_snapshots）；导出/恢复/reset 全联动。
- Codex 审查修补：omitted 计数 bug（窗口总数替换多取 1 条）+ 来源 ID 恢复后二阶段重映射。

## 验证（ZCode 最终验收，2026-09-06 20:0x）

- 后端聚合 **64/64**（6m42s；新增 test_fact_decay / test_ephemeral_privacy / test_future_letters / test_relationship_snapshots）
- 前端 Vitest **52/52**、vue-tsc 零错误、Vite/Electron 生产构建通过
- Playwright 浏览器关键路径 **6/6**（含密封信正文不进 DOM）
- ZCode 静态复审：衰减/临时对话/M8 双切片核心代码逐文件精读，无新增缺陷（db._lock 为 RLock 确认无嵌套死锁；artifacts 无向量索引故删除无幽灵向量；expires_at 字符串比较语义与召回闸门一致）

## 流程记录

- 派发模式首次完整走通：Codex zcode_exec（mode=edit）→ ZCode headless 落盘（M8.1 7.36M / M8.2 8.88M token，GLM 体验套餐）→ 信箱汇报 → Codex 审查修补。
- 双代理规范固化于仓库根 AGENTS.md 与 TECH-PLAN §8.12（分工/派发纪律/VERIFY 双层验证/权限模式）。
- worker v0.2.3 新增 VERIFY: 双层验证（受信任代码代跑任务书声明的验证命令）。
