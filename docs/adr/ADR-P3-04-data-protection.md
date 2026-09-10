# ADR P3-04：数据保护边界与 SQLCipher 接入顺序

- 状态：A/B/C 离线切片已验证；D–F 待实施
- 日期：2026-09-10
- 执行：Codex
- 决策依据：`docs/Zcode技术指导.md` P3-04 与 §21.1

## 目标与威胁边界

目标是在电脑丢失、数据目录或备份被复制时，攻击者不能直接读取用户内容；本机正常使用最终由 Windows DPAPI CurrentUser 解锁，换机使用独立恢复口令。应用锁与磁盘加密是两条独立控制：应用锁负责已启动进程的 UI/API 会话，SQLCipher 和文件容器负责静态数据。

不承诺抵御已经登录并能控制同一 Windows 用户的恶意进程，也不把普通 SSD 上的覆盖删除描述成可靠物理擦除。当前 A/B 切片只建立连接边界和临时目录 PoC，**没有迁移或加密现有用户数据**。

## 明文数据面清单

| 数据面 | 当前位置 | 敏感内容 | 后续处理 |
|---|---|---|---|
| 关系与业务库 | `data/bot.db` | 消息、事实、关系、日记、知识分块、生活事件 | SQLCipher，SQLite 继续作为事实权威 |
| 会话库 | `data/sessions.db` | 当前会话、归档正文、图片引用、解释快照 | SQLCipher |
| 任务库 | `data/agent_tasks.db` | 任务目标、步骤、日志、结果与产物路径 | SQLCipher |
| 本地诊断库 | `data/telemetry.db` | 去正文事件、散列作用域、结果类别 | SQLCipher；关闭与清理语义保持 |
| 向量缓存 | `data/chroma/`、`data/chroma_mem0/` | 文本、元数据、embedding | C 片迁入加密库表；内存构建检索索引，磁盘缓存可重建 |
| 用户媒体与附件 | `data/imgs/`、`data/documents/`、`data/screenshots/` | 用户图片、生成图、原始文档、截图 | C 片使用 MK 分域派生键的分块 AEAD 容器 |
| 人格私有资源 | `data/personas/` | 导入人格卡、生活模板、问候变体与活动状态 | C 片使用加密文件容器；内置仓库资源不重复加密 |
| 工具与运行日志 | `data/tool_log*.jsonl` 及进程日志 | 参数摘要、错误、路径和运行元数据 | C 片改为加密/最小化落盘并保持轮转 |
| 备份与迁移快照 | `data/backups/` | 三库快照、资源副本、manifest | F 片备份密文对象；manifest 不含密钥，恢复槽不进普通备份 |
| 临时文件与缓存 | 导入、转码、下载、语音等临时目录 | 处理中间数据 | C 片统一受控随机目录并在 `finally` 清理 |
| 配置与密钥 | `.env`、未来 `keyslots/` | 服务凭据、DPAPI 槽、恢复槽 | `.env` 不进备份；密钥槽与普通数据包分离 |

新增持久化位置必须更新本清单，并说明所有者、删除、备份和加密策略。

## A/B 切片决策

1. 后端所有 SQLite 打开操作统一走 `backend/storage/connect.py`。现阶段未提供密钥时仍使用标准库 SQLite，保持运行行为不变；静态测试阻止业务模块重新直接调用 `sqlite3.connect`。
2. 引入固定版本 `sqlcipher3==0.6.2`，其 Windows CPython 3.12 wheel 在本机提供 SQLCipher 4.12.0 Community。请求加密连接但驱动不可用或未处于 cipher 状态时立即失败，不回退明文。
3. 加密连接只接受内存中的 32-byte raw key。连接先关闭 SQLCipher 内部日志，再设置 key、检查 `cipher_version` 和 `cipher_status`，并读取 `sqlite_master` 强制验证密钥；密钥不从环境变量读取，不写命令行或日志。
4. `create_encrypted_copy` 只创建新目标，拒绝覆盖；通过 `sqlcipher_export` 从明文源生成副本，显式复制 `user_version`，随后比较 schema hash、逐表行数和有限样本 hash，并执行 `cipher_integrity_check` 与 `integrity_check`。失败删除不完整目标，源文件始终不改。
5. `scripts/sqlcipher_poc.py` 自己创建并销毁临时数据库，不接受真实数据路径。它证明加密文件没有 SQLite 明文头、正确 key 可读、错误 key 失败、schema/触发器/索引/行数与样本一致。

## C 切片决策

1. `backend/storage/file_container.py` 提供 format v1 分块 AEAD 容器。MK 仅由调用方以内存参数传入，经 HKDF-SHA256 按 `media/attachment/persona/log/backup/temporary/vector` 分域派生 256-bit data key；容器使用 AES-256-GCM，每块使用独立随机 96-bit nonce。
2. 全局 header 记录 `format_version/key_id/domain/object_id/chunk_size`，每块 header 记录 `format_version/key_id/nonce/chunk_index/final`，两层 header 都进入 AAD。解密严格检查块序、终块、尾随数据、长度和认证标签，错误 key、篡改、截断、换序都不能产出成功结果。
3. 文件对象以随机 128-bit id 命名为 `.tzenc`，不在路径或容器中保存原始文件名。加密、解密目标均以独占创建保留；失败删除本次创建的残件，绝不覆盖或删除调用方已有文件。`decrypt_to_bytes` 有默认 32MB 上限，只有整个容器验证完成才返回正文。
4. `backend/storage/vector_embeddings.py` 建立独立 SQLCipher 向量库，只保存稳定 `source_id/chunk_id/model_id`、向量 blob、内容 hash 和版本，不复制源文本。`vector_models` 锁定每个模型的维度；启动后解包到 `MemoryVectorIndex` 做确定性余弦线性检索，进程退出即丢弃内存索引。
5. C 仍是离线可组合件：当前运行时继续使用既有明文数据目录与 Chroma。D 提供正式 MK/key broker、E 完成迁移切换后，才允许把真实媒体和向量写入这些入口，并清理可重建的明文 Chroma 目录。

## 未完成边界

- D：MK、DPAPI 本机槽、Argon2id 恢复槽、应用锁和进程内 key broker。
- E：全局 persistence gate、迁移状态机、隔离进程校验与原子目录切换。
- F：同 generation 加密备份、空目录恢复、目标机新建 DPAPI 槽和恢复演练。

在 D/E 完成并通过用户数据副本演练前，不给运行时数据库传入密钥，也不删除任何明文原库。

## 验证记录

```text
.venv/Scripts/python.exe tests/test_data_protection.py
.venv/Scripts/python.exe tests/test_encrypted_storage.py
.venv/Scripts/python.exe scripts/sqlcipher_poc.py
.venv/Scripts/python.exe tests/test_schema_backup.py
.venv/Scripts/python.exe tests/test_relationship_bundle.py
```

真数据迁移记录：未执行（按计划留空）。
