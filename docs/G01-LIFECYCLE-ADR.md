# G01 分层消费与接手验证记录

执行：Codex，2026-09-08。依据：技术指导 §14.8、§22.3，以及体验升级总纲批次 3。本记录描述当前切片，不代表 G01 或全部 15 批次完成。

## 已落地

- `e807658` 接收 ZCode 的影子评分、注释和初历底座；修补来源归属、删除级联、恢复重映射与版本守卫。
- `c57a99d` 将评分接入 `daily.extract_facts`。提取器仅返回候选事实及用户消息编号；服务端限制来源到当轮实际输入，拒绝 assistant、跨人格和无效消息编号。同日重复只计一天，显式重要性由有来源的表达识别，重复提及已有事实复用原 id。
- `475f71d` 将 short 策略接入关键词检索、向量命中后的 SQLite 权威校验和 `decay_expired_facts`。实际删除仍走 `delete_fact_everywhere`，删除前持同一写锁复查固定状态和有效期限。

## 本片补充的实现决策

1. 期限保留在 policy 消费逻辑，绝不覆盖 `facts.expires_at`。已有期限完全优先；仅没有既有期限、非 pinned、非 legacy、已具有真实观察起点的 short 事实适用自动期限。long 不新增期限。
2. 自动期限从第一次真实观察起点计算，重复评分不后移起点。当前确定性类别识别：今天/今晚/此刻/临时为 1 天；最近/当前/这周/正在/这几天为 7 天；其余 short 为 30 天。14 天是已实跑的逻辑回放，不要求等待真实 14 天。
3. `memory_salience_enabled` 控制生产评分，`memory_lifecycle_enabled` 控制期限消费；均默认开启，分别支持 `FEATURE_MEMORY_SALIENCE_ENABLED` 与 `FEATURE_MEMORY_LIFECYCLE_ENABLED` 的部署初值，动态设置覆盖部署初值。关闭生命周期消费保留影子记录，不恢复已经物理删除的数据。
4. schema v25 为 memory_policy 增加 `source_message_ids` 与 `first_observed_at`。引用只存编号，不复制正文；消息不在关系包内，恢复时清空 policy 的消息编号，避免撞上目标库无关消息。reset 沿用三张侧表的清理，未新增 kv 键。
5. 未曾参与生产评分的旧事实不自动转为 short；仅新事实或有明确重要性表达的旧事实进入观察。无合法来源不获得来源加分；不得因评分提高置信度。到期后的旧 short 事实不能通过迟到评分恢复召回。

## 验证范围

接手基线全量：112/112，464.75 秒；前端 Vitest 69/69，Playwright 7/7，vue-tsc 与 Web/Electron 构建通过。全量执行期间按用户要求继续开发，因此不把该数字冒充最终所有新增代码的完整全量复验。

评分接线后的独立回归：memory_salience、memory_correction、schema_backup、ephemeral_privacy、relationship_bundle、flags_http，6/6。新增 v24→v25 迁移、旧库备份、恢复清空不可映射引用、14 天回放与事实提取→policy 真实调用链测试。

期限消费后的独立回归：memory_salience、memory_correction、fact_decay、ephemeral_privacy、relationship_bundle、pinned_retention、memory_v2、flags_http，8/8；删除前复查的最终改动另跑 memory_salience/fact_decay，2/2。

## 接手中修复的其他问题

- `0bec7c5`：关系事件正式失效入口没有清除 L03 气质证据；补真实入口回归。
- `1fe9c0e`：首屏 e2e 仍断言旧“陪伴中”文案；改为固定 presence 响应验证实时行程文案。
- `f2e57fd`：独立运行 test_memory_v2 没有默认隔离。修复前曾连接真实 data/chroma 和 bot.db，并触发 v24 升级；只读核查 smoke_test_user 的 users=1，messages/facts/long_memory=0。未擅自删除无法判断是否原有的记录；修复后隔离运行 11/11。
- `cff3c5f`：工具集成测试将工作文件放到系统 TEMP，被工作区路径限制正确拒绝；改用工作区内独立临时目录，不放宽应用路径限制。pytest 的旧临时链接有 Windows 权限错误，改用全新的 `--basetemp` 后全量正常退出。

## 未完成

关系锚点与初历加分目前仍只有底层参数和侧表，尚未接通全部生产事件来源；视角注释仍缺生产生成/确认路径；聊天解释层的有权限来源展开和可见遗忘尚待接线。源码中现有评分及期限消费不能作为这些体验已经完成的证据。下一片须按 §14.8 与 F07 的二次授权、已删除来源失效规则推进，不展示墓碑正文，不编造初历。
