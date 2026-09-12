# ADR P3-04：数据保护边界与 SQLCipher 接入顺序

- 状态：A/B/C/D 离线切片已验证；E–F 待实施
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

## D 切片决策（2026-09-12，执行：ZCode（GLM））

1. `backend/core/keyslots.py`：MK 为 256-bit `os.urandom`。本机槽 = DPAPI CurrentUser（`CryptProtectData`，描述串 `tztuzhan-mk`，`UI_FORBIDDEN`），落盘 `keyslots/local.dpapi`，写后原子替换；恢复槽 = Argon2id（本机标定 t=4/m=128MiB/p=4，实测约 120ms，参数随槽落盘并按记录执行，读取时做边界校验）派生 KEK，AES-256-GCM 包装 MK，落盘 salt/参数/nonce/ciphertext，无口令无 KEK。
2. 槽写入即自检：恢复槽落盘后立即实际解包，解不开就删除该槽（不留「看似存在实则解不开」的槽）。初始化在存在任意槽时拒绝（防静默换 MK）；轮换 = 用当前 MK 重建恢复槽，MK 不变。
3. `backend/core/key_broker.py`：进程内单例唯一持有 MK，**零知识接口**——业务侧只能按域派生 data key / 取数据库 key，不外泄 MK 本体；`lock()` 即清零并丢弃（尽力缩短生命周期，不宣称绝对内存清零）。
4. 应用锁三态（自行补充的决策，理由：D 片若两态会把从未初始化的用户在启动时锁死）：`inactive`（无 keyslots，中间件不拦任何请求——**现有部署零行为变化**）→ `unlocked` ⇄ `locked`。已初始化的用户重启后由 `engage_if_slots` 进入 `locked`，本机槽一键解锁或恢复口令解锁。
5. `backend/api/lock.py` + `app.py` 中间件：锁定态下除 `/api/lock*` 与 `/api/health` 外全部 423 `app_locked`；前端负责清空敏感 store/停 TTS/停流（「仅遮窗口不算锁定」——后端侧语义是忘钥匙）。恢复口令失败统一 401（不区分口令错/槽坏），15 分钟窗口 5 次失败后 429 限速。`/api/lock` 的 status 如实返回 `data_encrypted: false`（E 片前数据仍为明文，不把应用锁说成磁盘加密）。
6. 前端解锁界面属 E 片配套（迁移后才有解锁刚需）；D 片交付后端语义与 API，`initialize` 仅在无任何槽时可用。

## E 片决策（2026-09-12，执行：ZCode（GLM）——迁移引擎，运行时接线待续）

1. `backend/storage/migration.py`：状态机 unencrypted → preparing → verified → switched → cleanup_pending → encrypted（任一步失败 → failed，暂存清理、原目录分毫不动）。preparing = 三库先 WAL 收敛 + backup API 一致性快照（迁移集内独立副本）+ 逐库 `create_encrypted_copy`（B 片原语，内含 manifest 等价校验）；verified = **隔离子进程**双 key 校验（错误 key 必拒、正确 key 必过；MK 只经 stdin 传给子进程，不进 argv/env/磁盘）+ 资产按 C 片容器加密（imgs/screenshots=media、documents=attachment、personas=persona，映射关系与原相对路径记入 asset-index.json）；switched = 同卷两次 rename 原子切换，第二次失败立即挪回。
2. 可重建产物不迁移并在 journal 记录理由：chroma/chroma_mem0（C 片 vector_embeddings 接管）、tts_cache、telemetry.db、历史明文备份（F 片接管加密备份）、派生日志。
3. 明文目录在 switched/cleanup_pending 阶段完整保留为 `data.plaintext-<stamp>`；`finish_cleanup` 是显式独立动作（用户确认后调用），永不随迁移自动删除。
4. journal（encryption-migration.json）存于数据目录**父级**，跨目录切换存活；failed 后允许重跑，cleanup_pending/encrypted 期间拒绝重入。引擎内曾有一处真实缺陷被回归抓出：业务 MigrationError 走直通分支绕过 fail() 落账，状态会卡在中间态——已修。
5. **运行时接线（2026-09-12，ZCode）**：`backend/storage/runtime.py`——加密态判定（journal 指纹缓存）+ 密钥分发（锁定态抛 `DatabaseLockedError`）；userdb 惰性开库（`conn` 属性首次访问触发，明文模式语义不变）、session/agent 库建表初始化从导入期改为首次连接前、telemetry 锁定态静默跳过；锁定端点调用 `close_all_databases()` 关闭库连接（忘钥匙的存储侧落实）；维护循环在加密模式跳过明文 checkpoint/备份（F 片接管）。
6. **双驱动异常兼容**：sqlcipher3 异常与 sqlite3 是平行体系（互不继承），`storage/connect.OPERATIONAL_ERRORS` 提供双元组，幂等兼容 DDL（「列已存在则跳过」）统一引用——否则加密库上 schema 兼容逻辑全部失效（回归抓出）。
7. **迁移目录切换与日志句柄**：loguru 的 `bot.log` sink 会钉住数据目录（Windows rename 零容忍）；引擎在 rename 重试前释放 sink、两步 rename 结束后按新 data root 重建（过早 restore 会把刚挪走的目录「复活」，回归抓出）。keyslots 随迁移复制进暂存区（DPAPI/口令保护密文，明文复制无风险），否则切换后锁定/解锁失效。

## 未完成边界

- E 运行时接线：全局 persistence gate、锁定态延迟开库、启用加密 API/UI、真实数据迁移执行。
- F：同 generation 加密备份、空目录恢复、目标机新建 DPAPI 槽和恢复演练。

在 E 完成并通过用户数据副本演练前，不给运行时数据库传入密钥，也不删除任何明文原库。D 片的 broker 是「随时可挂钥匙的锁架」，不改变现有数据路径。

## 验证记录

```text
.venv/Scripts/python.exe tests/test_data_protection.py
.venv/Scripts/python.exe tests/test_encrypted_storage.py
.venv/Scripts/python.exe scripts/sqlcipher_poc.py
.venv/Scripts/python.exe tests/test_schema_backup.py
.venv/Scripts/python.exe tests/test_relationship_bundle.py
.venv/Scripts/python.exe tests/test_keyslots.py
.venv/Scripts/python.exe tests/test_app_lock.py
```

D 片验证（2026-09-12，ZCode）：`test_keyslots.py` 五组（双槽往返/统一失败与二次输入/轮换/重复初始化与损坏槽/边界）与 `test_app_lock.py` 六组（inactive 不拦/锁定 423 与白名单/限速/忘钥匙语义/重启锁定态/参数校验）全绿；suite runner 复跑 data_protection、encrypted_storage、schema_backup、flags_http、m4_relationship 无回归。真实 `data/keyslots` 未创建（未调用 initialize），运行时行为与 D 片前完全一致。

真数据迁移记录：未执行（按计划留空）。
