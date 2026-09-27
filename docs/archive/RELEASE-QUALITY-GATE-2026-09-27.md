# 菟菚桌面助手 v3.5.0 发布质量门禁报告

> 执行：ZCode（GLM）

- 报告时间：2026-09-27（Asia/Shanghai）
- 报告人：ZCode（GLM）
- 工作区：`D:\TZtuzhanAssistant`
- 审查基线 HEAD：`8ff23e8`（ST 卡导入向导）；发版提交与 `v3.5.0` tag 在本报告之后生成
- 发布对象：菟菚桌面助手 `v3.5.0`，schema **v46**（manager_memories 管理记忆权威表），Windows x64 Electron 桌面壳 + 本地 Python 后端
- 版本跨度：`v3.1.0`（2026-09-20）以来 **97 个提交**（NP 新手保护批 14/14、BUG-HUNT 103 项全修、D9-D12 内在状态四件、L09/L10 本地语音与桌面骨架、P3-04 应用锁等）

## 结论

**Go（面向 GitHub Releases 公开发布）**

依据：五项自动化门禁全绿（见下表）；两处 E2E 失败已定位为测试定位器失配于 NP-09/NP-12 的正当产品改动并修复，非产品缺陷。与 v3.1.0 门禁相同的遗留风险继续如实披露：安装包未做 Authenticode 代码签名（SmartScreen 首次提示，个人项目风险已接受）；真机 UAT 与真实模型体验未纳入本轮自动化门禁（由作者日常使用覆盖）。

## 自动化门禁结果（2026-09-27 全量实跑）

| 门禁 | 结果 | 耗时 | 说明 |
|---|---|---|---|
| 后端聚合回归（pytest + suite runner） | **203/203 通过** | 36m43s | 含 NP 批次、BUG-HUNT 修复、D9-D12、P3-04 应用锁全部回归 |
| 前端 Vitest | **200/200 通过**（41 文件） | 3.5s | |
| vue-tsc + Vite/Electron 生产构建 | 通过 | — | `npm run build` |
| 浏览器 E2E（Playwright） | **15/15 通过** | 25.7s | 修复 2 处测试定位器失配后全绿（见下节） |
| 桌面外壳冒烟（真实 Electron 拉起） | **5/5 通过** | — | IPC 读到版本号 3.5.0（package.json 同步验证） |
| 人格确定性 eval | **53/53 通过（100%）** | — | `scripts/run_persona_eval.py` 确定性模式（红线 + 行为签名） |

## E2E 事件记录（测试失配，非产品缺陷）

1. **设置开关用例**（`critical-paths.spec.ts:274`）：NP-12 数据护栏在设置面板新增第二个 `role="status"` 元素（「最新备份校验通过」提示），原 `getByRole('status')` 单元素断言触发 Playwright 严格模式冲突。开关本身保存成功。修复：改按文案 `getByText('已保存并立即生效')` 定位。
2. **记忆纠偏用例**（`critical-paths.spec.ts:298`）：NP-09 将「忘掉」确认从 `window.confirm` 换为应用内 ConfirmDialog，测试的 `window.confirm = () => true` 替身失效导致删除未确认。编辑/固定/置信度展示全链路实际通过。修复：改点击 `alertdialog`（`确定让菟菚忘掉这条？`）中的「确定」。

**经验教训（给后续运行者）**：`npx playwright test` 直接调用会复用 `.tmp/e2e-datadir` 指针指向的旧目录——若上一个 run 的应用锁用例初始化过 keyslots，新 run 会冷启动即处于锁定态（423 + 全屏遮罩），产生与本次无关的批量假失败。**必须通过 `npm run test:e2e` 入口运行**（它会先删除指针文件），本轮第二次误跑已实证该坑。

## 发布制品

- `deploy/TZtuzhanAssistant-Deploy-v3.5.0.zip`（轻量包，bge-small-zh-v1.5）
- `deploy/TZtuzhanAssistant-Deploy-Full-v3.5.0-Large.zip`（大杯包，bge-m3）
- `frontend/release/菟菚桌面助手 Setup 3.5.0.exe` → 以 `TZtuzhanAssistant-Setup-v3.5.0-win-x64.exe` 名义上传

打包方式：`scripts/build_deploy.ps1`（-Version 3.5.0，从发版提交后的源码树构建）与 `npm run dist:win`。产物不入库，只经 GitHub Releases 交付。

## 升级与迁移

- schema v42 → v46：升级前自动快照备份到 `data/backups/schema-*`；旧好感度自动换算信任×亲密。
- 部署包覆盖安装前须备份 `data/` 目录（README 与包内使用说明均已标注）。
- P3-04 应用锁对既有部署零行为变化（未初始化 keyslots 时为 inactive，中间件不拦请求）。

## 未覆盖项（如实披露）

- 真实 LLM 全链路体验、Win10/Win11 多真机兼容矩阵、加密备份恢复演练：未纳入本轮自动化，沿用作者日常使用与 v3.1.0 以来连续修复的实机反馈。
- 依赖无升级（本批次未动 `requirements.txt` / `package.json` 依赖版本），npm audit 状态与 v3.1.0 一致。
