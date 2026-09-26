# 菟菚桌面助手 v3.1.0 发布质量门禁报告

- 报告时间：2026-09-18（Asia/Shanghai）
- 报告人：Codex
- 工作区：`D:\TZtuzhanAssistant`
- 当前 HEAD：`833bfac`
- 远端基线：`origin/main` = `e1beeda`
- 发布对象：菟菚桌面助手 `v3.1.0`，schema v42，Windows x64 Electron 桌面壳 + 本地 Python 后端
- 关键制品：`D:\TZtuzhanAssistant\frontend\release\菟菚桌面助手 Setup 3.1.0.exe`

## 结论

**Conditional Go（条件性准出，仅限内测/受控发布）**

依据：后端全量回归、前端类型检查/单测、浏览器 E2E、真实 Electron 外壳冒烟、最终 NSIS 打包、依赖锁一致性、产物敏感内容扫描均已完成；当前未发现功能性阻塞缺陷。由于源码尚未形成可追溯提交、安装包未做 Authenticode 签名、ChromaDB 安全公告尚未由风险接受人签字，且真机 UAT、真实模型体验、恢复演练和生产监控责任未完成，不能判定为无条件 `Go`，也不能直接公开发布。

公开发布转为 `Go` 的最低条件：

1. 将本轮待发布源码形成可追溯提交和 tag，并从该提交重新构建、复算哈希。
2. 使用正式代码签名证书签署安装包；若决定不签名，必须由具名风险接受人书面接受 SmartScreen/供应链风险。
3. 完成并使用具名签字的真机 UAT、真实 LLM 体验、Win10/Win11 兼容、安装/升级/卸载验收。
4. 完成真实加密备份恢复、密钥丢失恢复和发布级回滚演练。
5. 对 ChromaDB 安全公告形成“本地 PersistentClient 架构不适用”的具名风险接受，或升级到含修复版本。
6. 指定回滚决策人、回滚操作人、监控责任人和发布值班安排。

任一条件未满足时，公开发布状态应保持 `No-Go`。

## 发布范围与目标环境

- 目标系统：Windows x64 桌面端；安装包为 NSIS `oneClick`、`perMachine=false`。
- 目标用户路径：本地单机、本地数据、本地/远程可配置模型服务。
- 本轮变更面：加密迁移与锁定态启动、加密备份、effect ledger/降级计数、turn context/effect/prompt 拆分、恢复脚本、后端维护循环、CI、前端 Electron 44 升级与桌面外壳冒烟。
- 安装包边界：打包 Electron 壳、前端构建产物、后端源码、插件、skills、requirements 与示例配置；不内置 Python 运行时和大型模型权重。
- 真实数据边界：`D:\TZtuzhanAssistant\data\` 是用户真实加密数据，本轮测试均使用 `TZTUZHAN_DATA_DIR` 指向临时目录；未修改真实数据。

## 测试矩阵

| 优先级 | 测试类型 | 范围 | 环境 / 方法 | 结果与证据 | 状态 |
| --- | --- | --- | --- | --- | --- |
| P0 | 后端全量回归 | 后端、存储、迁移、加密、API、主动性、记忆与边界用例 | `.venv`，`MEMORY_EMBED_FORCE=1`，临时 `TZTUZHAN_DATA_DIR`，`python -m pytest tests/ -q` | `175 passed in 401.43s`；`D:\TZtuzhanAssistant\.tmp\qa-gate-full-venv-20260918-2012.log` | 已验证 |
| P0 | CI 同构后端回归 | 与 GitHub Actions 依赖子集一致的轻量 venv | `python -m pytest tests/ -q --basetemp=...` | `175 passed in 269.20s`；`D:\TZtuzhanAssistant\.tmp\qa-gate-full-20260918-1944.log` | 已验证 |
| P0 | 前端类型检查 | Vue/TypeScript 全量静态检查 | `npx vue-tsc --noEmit` | exit 0；`D:\TZtuzhanAssistant\.tmp\qa-gate-frontend-typecheck.log` | 已验证 |
| P0 | 前端单元测试 | 25 个测试文件 | `npm test`（Vitest 4.1.11） | `25 files / 115 tests passed`；`D:\TZtuzhanAssistant\.tmp\qa-gate-frontend-unit.log` | 已验证 |
| P0 | 浏览器端到端 | 对话流、面板、无障碍、人格、阅读、记忆、知识库、锁定/解锁等 15 条关键路径 | `npm run test:e2e`，真实后端 + Playwright | `15 passed in 14.3s`；`D:\TZtuzhanAssistant\.tmp\qa-gate-frontend-e2e.log` | 已验证 |
| P0 | 桌面外壳冒烟 | 主进程、preload、IPC、随包后端、首屏 DOM | Electron `v44.4.2`，CDP 真实启动，临时数据目录 | `5/5 PASS`；`D:\TZtuzhanAssistant\.tmp\qa-gate-electron-smoke.log` | 已验证 |
| P0 | 包构建 | 生产前端构建、Electron 打包、NSIS 安装包 | `npx electron-builder --win` | exit 0，326 个归档文件，7-Zip 归档测试 `Everything is Ok`；`D:\TZtuzhanAssistant\.tmp\qa-gate-nsis-final.log`、`qa-gate-nsis-archive-test.log` | 已验证 |
| P0 | 产物泄露扫描 | 安装包 payload 中的真实 API key/token/secret/password、数据库、keyslot、恢复密钥 | 以 `.env` 中 4 个敏感值与 release 326 个文件逐一比对 | 敏感值匹配 0，真实数据库/密钥文件匹配 0；`D:\TZtuzhanAssistant\.tmp\qa-gate-release-content-scan-final.txt` | 已验证 |
| P0 | `app.asar` 内容检查 | 前端打包内容与敏感路径 | `asar list` 后匹配 env/db/keyslot/recovery | 669 个条目，敏感路径 0；`D:\TZtuzhanAssistant\.tmp\qa-gate-asar-files-final.txt` | 已验证 |
| P0 | 测试数据隔离 | 验证真实 `data/` 未被本轮测试改写 | 测试前后临时数据目录隔离；检查真实数据修改时间 | 20:00 后真实 `data/` 修改文件数 0；`D:\TZtuzhanAssistant\.tmp\qa-gate-data-integrity.txt` | 已验证 |
| P1 | 依赖锁一致性 | `package.json` 与 `package-lock.json` | `npm ci --dry-run --ignore-scripts` | exit 0；`D:\TZtuzhanAssistant\.tmp\qa-gate-npm-ci-dry-run.log` | 已验证 |
| P1 | Linux CI 定义 | GitHub Actions YAML 与后端/前端 job | YAML 解析 | `yaml_ok jobs=backend,frontend`；`.github/workflows/regression.yml` | 已验证 |
| P1 | 前端依赖安全 | npm 生产与开发依赖 | `npm audit --json` | 0 漏洞（0 critical / 0 high / 0 moderate）；`D:\TZtuzhanAssistant\.tmp\qa-gate-npm-audit-final.json` | 已验证 |
| P1 | Python 依赖安全 | `requirements.txt` | `pip-audit -r requirements.txt` | ChromaDB 1.5.9 命中 4 个唯一公告、5 条记录且无 fix version；当前仅走 `chromadb.PersistentClient`，未暴露 HTTP Server；`D:\TZtuzhanAssistant\.tmp\qa-gate-pip-audit.json` | 部分验证 |
| P1 | 源码密钥启发式扫描 | 非构建源码中的常见密钥模式 | `rg` 启发式扫描 | 唯一命中为 `tests/security/test_secret_redaction.py` 的假 key fixture；`.env` 被 `.gitignore` 忽略且未跟踪 | 已验证 |
| P1 | 加密迁移/备份测试 | 加密库打开、锁定态、加密备份、恢复脚本 | 已包含在 175 条后端回归中 | 自动化测试通过；真实备份/恢复演练尚未执行 | 部分验证 |
| P1 | 回滚能力 | 恢复脚本、备份清单、旧版安装包保留 | 自动化测试 + 人工检查 | 脚本与测试通过；未做发布级“升级后回滚到旧版 + 数据恢复”完整演练 | 部分验证 |
| P1 | 安装包签名 | Authenticode、SmartScreen 信任链 | `Get-AuthenticodeSignature` | 安装包和主程序均为 `NotSigned`；`D:\TZtuzhanAssistant\.tmp\qa-gate-signing.txt` | 阻塞 |
| P1 | 发布可追溯性 | 制品能否追溯到唯一 Git 提交 | `git status` / `git rev-parse` | HEAD 为 `833bfac`，但仍有已修改/未跟踪的待发布源码，制品来自 dirty working tree | 阻塞 |
| P1 | 监控与告警 | 生产监控、告警通知、值班链路 | 代码/配置审计 | 有本地日志与 `GET /api/meta` 的 `effect_stats`；无外部告警和具名值班链路 | 部分验证 |
| P2 | 性能与容量 | 冷启动、长会话、大量记忆、真实 embedding、并发与资源上限 | 无发布负载模型与验收阈值 | 未建立可判定的容量结论 | 未验证 |
| P2 | Windows 兼容性 | Win10/Win11、DPI、多显示器、休眠唤醒、托盘/通知 | 真机矩阵 | 需在目标机器执行 | 待人工验收 |
| P2 | 真实 LLM 体验 | 中文质量、长期上下文、人格稳定性、记忆正确性、失败降级 | 真实模型与真实用户场景 | 需业务/产品验收 | 待人工验收 |

## 最终制品与哈希

最终 NSIS 安装包：

- 路径：`D:\TZtuzhanAssistant\frontend\release\菟菚桌面助手 Setup 3.1.0.exe`
- 大小：142,260,616 bytes
- SHA256：`92237FAF6A4941774DD85FDBE6CD29F606BE48CB5A9072DEE34FA1090E7C03D8`

Blockmap：

- 路径：`D:\TZtuzhanAssistant\frontend\release\菟菚桌面助手 Setup 3.1.0.exe.blockmap`
- SHA256：`FBE5E1F4D7B3C04547CC8FA3B75696E0FB32806405771B6331DC21608449D17F`

`app.asar`：

- 路径：`D:\TZtuzhanAssistant\frontend\release\win-unpacked\resources\app.asar`
- 大小：30,838,866 bytes
- SHA256：`F3C4F6F3AFD08F16D1E5EF619F41013A6B75821AEC9A96BB9EC3DFC5CC1255B8`

哈希清单：`D:\TZtuzhanAssistant\.tmp\qa-gate-release-hashes.json`

## 已覆盖

- P0 核心后端回归与前端关键链路。
- Electron 真实进程、preload、IPC、随包后端与首屏 DOM。
- 生产构建与 NSIS 安装包生成。
- 安装 payload 的敏感文件和真实环境变量泄露检查。
- 前端依赖锁一致性、npm SCA、Python SCA。
- 真实加密数据目录不被测试进程改写的隔离验证。
- 加密迁移/备份相关自动化测试。
- CI workflow YAML 与 job 结构。
- 产物版本、包元数据、大小和 SHA256 固定。

## 未覆盖

- 最终安装包在干净 Win10/Win11 机器上的安装、升级、卸载和 SmartScreen 行为。
- 正式 Authenticode 签名及证书链验证。
- 真实 Python 运行环境首次部署、缺失依赖提示和用户操作路径。
- 真实本地/远程 LLM、真实 embedding 模型下的质量、延迟、Token/成本和降级行为。
- 长会话、大量记忆、Chroma 容量、休眠唤醒、多显示器和 DPI 兼容。
- 生产级监控、告警、崩溃上报、值班响应和发布值班表。
- 发布级数据恢复/回滚演练。
- 隐私政策、法律合规、第三方服务边界和用户数据处理签字。
- 最终发布提交/tag 与制品从该提交的可重复构建证明。

## 阻塞项

1. **代码签名缺失**：安装包和主程序均为 `NotSigned`。公开发布前必须签名，或由具名风险接受人书面接受相关风险。
2. **发布源码未形成唯一提交**：当前制品来自 dirty working tree，不能从远端或 tag 精确复现。公开发布前必须选择性提交本轮变更、建立 tag，并从该提交重建和复算哈希。
3. **ChromaDB 安全风险接受缺失**：4 个唯一公告均无修复版本。当前代码只使用本地 `PersistentClient`，未使用 Chroma HTTP Server、远端租户 RBAC 或 `trust_remote_code`，因此本架构下判定为 `不适用`；若引入 Chroma Server 或远端模型，必须重新评估。
4. **真实数据恢复演练缺失**：自动化测试不能替代真实加密库、真实 keyslot 与真实恢复密钥的恢复演练。

## 待人工验收

- 真实 UAT：完整聊天、主动性、记忆、人格切换、应用锁定/解锁。
- 真实 LLM：中文聊天质量、长期上下文、人格稳定性、记忆正确性。
- 真机兼容：Win10/Win11、无 Python 环境、DPI、多显示器、休眠唤醒、托盘和通知。
- 性能容量：启动时间、内存、长会话、大量记忆、真实 embedding。
- 数据安全：真实加密备份创建、恢复演练、密钥丢失恢复流程。
- 安装包：NSIS 安装、覆盖升级、卸载、残留检查、SmartScreen 行为。
- 合规与隐私：本地数据、加密、日志、第三方服务边界和法律审核。
- 监控告警：生产环境实际告警链路、责任人和值班安排。
- 发布审批：UAT 具名签收、风险接受人、回滚决策人。

## 剩余风险

| 风险 | 影响 | 概率 | 等级 | 缓解措施 | 负责人 | 截止时间 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 安装包未签名 | SmartScreen 警告、用户信任和供应链验证不足 | 高 | 高 | 使用正式证书签名；无法签名则书面接受风险 | 待指定 | 公开发布前 | 阻塞 |
| 制品来自未提交源码 | 无法证明制品与源码一致，难以精确回滚 | 高 | 高 | 选择性提交、打 tag、从 tag 重建并冻结哈希 | 待指定 | 公开发布前 | 阻塞 |
| ChromaDB 无修复公告 | 若未来暴露 Chroma Server，可能触发远程代码执行/越权 | 低（当前本地 PersistentClient） | 高（条件触发） | 保持仅本地 PersistentClient；禁止暴露 HTTP API；引入 Server 前升级或隔离 | 待指定 | 架构变更前 | 部分验证 |
| 未做真实恢复演练 | 密钥丢失或恢复路径失败可能造成永久数据不可用 | 中 | 高 | 使用真实加密数据副本完成备份、恢复、密钥丢失演练 | 待指定 | 发布前 | 待人工验收 |
| 真实模型性能未知 | 长上下文下延迟、内存、成本和失败降级不达标 | 中 | 中 | 建立负载模型和模型矩阵，采集 P50/P95/P99、RSS、失败率 | 待指定 | 发布前 | 未验证 |
| Windows 兼容矩阵未知 | 部分系统无法启动、托盘/通知/DPI 异常 | 中 | 中 | 在 Win10/Win11 真机执行安装与交互矩阵 | 待指定 | 发布前 | 待人工验收 |
| 监控与告警不足 | 用户问题无法及时发现和定位 | 中 | 中 | 明确本地日志保留、崩溃上报和问题分级；单机产品明确不适用外部值班告警 | 待指定 | 发布前 | 部分验证 |

## 回滚触发条件

满足任一条件应停止发布或回滚：

- 启动无法完成，或核心健康检查持续失败。
- 应用锁定/解锁失败，或真实加密库无法打开。
- schema 迁移失败、数据校验失败、恢复脚本无法恢复。
- 安装/升级/卸载导致用户数据损坏、丢失或残留不可接受。
- 安装包哈希与冻结值不一致，或安装包被替换/未签名且无风险接受。
- 真实 LLM/后端关键链路 P95 严重超阈值、持续崩溃或内存无限增长。
- 真机 UAT 发现 P0/P1 缺陷且无明确修复和复验结论。

## 回滚与监控准备状态

- 已保留历史安装包：2.0.0、2.1.0 仍位于 `frontend\release`。
- 已具备恢复脚本和加密备份自动化测试。
- 迁移 journal：`D:\TZtuzhanAssistant\encryption-migration.data.json`，当前状态为 `encrypted`。
- 真实 keyslots 未在测试中被修改；完整性证据见 `D:\TZtuzhanAssistant\.tmp\qa-gate-data-integrity.txt`。
- 回滚链路尚未做发布级演练，不能将自动化测试等同于已演练。
- 当前单机产品的日志和 `/api/meta.effect_stats` 可作为本地诊断，但生产告警责任人和通知链路尚未建立。

## 责任人 / 决策人

- 自动验证执行：Codex
- 发布决策人：待指定
- UAT 签收人：待指定
- 代码签名责任人：待指定
- 数据恢复演练负责人：待指定
- 回滚决策人/执行人：待指定
- 监控与值班负责人：待指定
- ChromaDB 风险接受人：待指定

## 证据索引

- 后端完整 `.venv` 回归：`D:\TZtuzhanAssistant\.tmp\qa-gate-full-venv-20260918-2012.log`
- 后端 CI 同构回归：`D:\TZtuzhanAssistant\.tmp\qa-gate-full-20260918-1944.log`
- 前端类型检查：`D:\TZtuzhanAssistant\.tmp\qa-gate-frontend-typecheck.log`
- 前端单测：`D:\TZtuzhanAssistant\.tmp\qa-gate-frontend-unit.log`
- 浏览器 E2E：`D:\TZtuzhanAssistant\.tmp\qa-gate-frontend-e2e.log`
- Electron 外壳冒烟：`D:\TZtuzhanAssistant\.tmp\qa-gate-electron-smoke.log`
- 最终 NSIS 构建：`D:\TZtuzhanAssistant\.tmp\qa-gate-nsis-final.log`
- 归档完整性：`D:\TZtuzhanAssistant\.tmp\qa-gate-nsis-archive-test.log`
- 产物哈希：`D:\TZtuzhanAssistant\.tmp\qa-gate-release-hashes.json`
- 产物泄露扫描：`D:\TZtuzhanAssistant\.tmp\qa-gate-release-content-scan-final.txt`
- `app.asar` 清单：`D:\TZtuzhanAssistant\.tmp\qa-gate-asar-files-final.txt`
- 依赖锁一致性：`D:\TZtuzhanAssistant\.tmp\qa-gate-npm-ci-dry-run.log`
- npm 审计：`D:\TZtuzhanAssistant\.tmp\qa-gate-npm-audit-final.json`
- Python 审计：`D:\TZtuzhanAssistant\.tmp\qa-gate-pip-audit.json`
- 签名状态：`D:\TZtuzhanAssistant\.tmp\qa-gate-signing.txt`
- 真实数据隔离与完整性：`D:\TZtuzhanAssistant\.tmp\qa-gate-data-integrity.txt`
- CI 定义：`D:\TZtuzhanAssistant\.github\workflows\regression.yml`