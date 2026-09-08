# ADR：P3-02C 有来源的知识观点

状态：已采用，2026-09-08。实现提交以 Git 历史为准。

## 决策

知识文档原文与角色观点分开存储。`knowledge_opinions` 只保存菟菚或用户确认的观点文本、所属文档、来源类型、置信度、版本和状态；`knowledge_opinion_sources` 保存观点到 `kb_chunks` 的片段范围及内容哈希。观点不进入 `facts`、`long_memory` 或用户画像，也不能作为独立来源证明外部事实。

同一用户、文档、规范化观点和来源组合由 `opinion_hash` 幂等约束。撤销只推进状态与版本；再次明确保存时复活原记录。读取时重新检查文档、分块、范围和片段哈希，任何来源缺失或改版都会使观点停止注入。

语境接入复用 `context_registry.KnowledgeOpinionsProvider`，仅在当前查询与观点有词面交集时产生候选，继续受统一预算、sticky/cooldown 和成功回合提交约束。渲染文本明确标识这是角色观点，不是用户事实或已验证外界事实。

## 迁移、导出与删除

bot.db schema 从 v21 升到 v22，只新增两张空表和索引，不回填或推测历史观点。两表加入 userdb reset 降级清单和全量 reset 清单。关系包的 knowledge 类别先恢复 `kb_documents/kb_chunks`，再恢复观点和来源，并重映射 document、opinion、chunk 三类 id。

删除知识文档时，先删除观点来源和观点，再删除分块、文档与向量；不会保留观点正文作为孤立副本。若需回滚代码，可保留新表不读；若需物理回滚，先导出可验证关系包，再删除两张表并把 schema 标记恢复到受支持版本，不能在真实库上直接覆盖降级。

## 验证

`tests/test_knowledge_opinions.py` 覆盖来源授权、片段边界、幂等、撤销/复活、相关语境、hash 失效、API 列表、导出恢复 id 重映射和文档删除。另回归知识库、关系包、schema 备份及全量测试。
