# NP 批次任务书（v2）：新手保护层 + 输入地基修复 + 拍板解锁批

> 制定：ZCode（GLM），2026-09-27。依据：2026-09-26/27 四轮产品分析（老玩家视角 ×3 + 新玩家视角 ×1）中已代码核实的结论。
> 执行分工（按 AGENTS.md 2026-09-07 指示）：功能实现由 **ZCode** 承接；**Codex** 负责独立代码审查、验证与验收提交。任务书亦可原样派发给 Codex worker（写盘任务必须 `mode=edit`）。
> 每个 NP-XX 一个主题、一个独立提交。提交信息格式：`feat: NP-XX 一句话` 或 `fix: NP-XX 一句话`（与仓库现有风格一致）。

## 批次全局禁区（每个任务都适用）

- 派发/动工前先 `git status`：**未提交文件一律不碰**。当前已知未提交：`tests/test_local_tts.py`（Codex 工作中）。
- 不升级任何依赖、不改 `package.json` / `requirements.txt` / 构建配置。
- diff 卫生：不做全文件重排版、不动无关行；前端任务不碰后端，NP-03 后端部分不碰对话管线（`core/pipeline.py` 等）。
- 文案改动保持中文、保持现有 aria-label/title 可访问性习惯（参考 `ChatInput.vue` 既有写法）。
- 严格遵守 `AGENTS.md` 协作纪律：对方 30 分钟内有活动时只读不写。

## 建议执行顺序

- 批次一：NP-01 → NP-02（同文件 `ChatInput.vue`，可一个会话连续做完、分开提交）→ NP-04 → NP-05 → NP-03 → NP-06（最大，最后做）。
- 批次二（拍板解锁，见文末）：NP-07 → NP-09 → NP-12 → NP-10 → NP-11（依赖 NP-10 的托盘/快捷键基建与 NP-06 的宠物状态查询）→ NP-08 → NP-13 → NP-14（动 probe 契约，最后做）。

---

## NP-01 中文输入法回车误发送（IME composition 守卫）

**背景**：`ChatInput.vue:166` 使用 `@keydown.enter.exact.prevent="emit('send')"`，无 `isComposing` 防护。拼音输入法按 Enter 确认候选词时 keydown 照常到达，消息会带着未上屏内容被提前发送。中文产品的地基 bug。

**涉及文件**（修改）：
- `frontend/src/components/ChatInput.vue`

**技术路线**：
1. 把行内 `@keydown.enter.exact.prevent` 改为绑定方法，如 `@keydown.enter.exact.prevent="onEnterKey"`；
2. 方法内守卫：
   ```ts
   function onEnterKey(e: KeyboardEvent) {
     if (e.isComposing || e.keyCode === 229) return
     emit('send')
   }
   ```
   （`keyCode === 229` 兜底旧实现；`Shift+Enter` 换行行为不变。）
3. `npm run type` 层面无新类型问题。

**非目标**：不改 busy 锁逻辑（那是 NP-02）、不改粘贴/图片上传路径、不做其他快捷键。

**验收**：
- 新增 `frontend/src/components/__tests__/ChatInput.ime.test.ts`：模拟 `isComposing: true` 的 Enter keydown → 不触发 send；模拟普通 Enter → 触发 send；`Shift+Enter` → 不触发 send 且不阻止默认换行。
- 手测路径（记录在回信）：微软拼音 / 搜狗候选确认不误发。

**验收命令**：
```
VERIFY: cd frontend && npx vitest run src/components/__tests__/ChatInput.ime.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：0.5h。

---

## NP-02 生成期间允许打字（解锁输入框 busy 锁）

**背景**：`ChatInput.vue:166` textarea `:disabled="busy"`，流式回复期间整个输入框禁用，用户不能预打下一句；主动消息也只能排队。新玩家解读为"卡了"。

**涉及文件**（修改）：
- `frontend/src/components/ChatInput.vue`
- 如发送守卫在父层：`frontend/src/components/ChatView.vue`（只加守卫，不动流式逻辑）

**技术路线**：
1. 去掉 textarea 的 `:disabled="busy"`，打字/草稿随时可用；
2. 发送路径保持禁止：Enter 分支与发送按钮在 `busy` 时 no-op（按钮维持现有禁用态；Enter 在 `onEnterKey` 中加 `if (props.busy) return`）；
3. 确认生成期间收到 send 事件时父层行为安全（现状应已忽略，验证即可）；
4. STT 回填草稿、图片上传按钮行为不受影响。

**非目标**：不做"生成中排队发送"（队列是独立设计，不在本片）、不改取消/停止按钮。

**验收**：
- 新增/扩展 `frontend/src/components/__tests__/ChatInput.ime.test.ts`（或独立文件）：`busy=true` 时可输入文本、Enter 不触发 send、按钮 disabled。
- 手测：流式回复期间打字不丢字、草稿保留到回复结束。

**验收命令**：
```
VERIFY: cd frontend && npx vitest run src/components/__tests__ --reporter=dot ;; cd frontend && npx vue-tsc --noEmit
```

预估：0.5~1h。

---

## NP-03 会话重命名（修"五个同名会话"导航瘫痪）

**背景**：自动命名撞车（截图中五条「聊聊菟丝子吧」），`SessionList.vue` 无重命名入口。已核实：`backend/api/sessions.py` 现有路由不含 rename；`backend/session/store.py` 的 `sessions`/`archives` 表均有 `title` 字段——**无 schema 变更**。

**涉及文件**（修改）：
- `backend/session/store.py`：新增 `rename_session(session_id: str, title: str) -> bool`（UPDATE 对应表 title 字段，`title.strip()` 非空、截断到现有上限 20 字符不变？——**自行补充决策**：自定义标题建议上限 60 字符，与自动标题 20 字上限区分；回信中标注）；
- `backend/api/sessions.py`：新增 `POST /sessions/{session_id}/rename`，body `{"title": str}`；`session_id == "current"` 改 `sessions` 表，否则改 `archives` 表；未知 id 返回 404；遵循该文件现有错误处理风格；
- `frontend/src/components/SessionList.vue`：标题旁铅笔入口或双击标题 → 行内编辑 → 调 API → 刷新列表；失败要有可见提示（不得静默吞错）；
- `frontend/src/api/`：对应客户端方法。

**关键既有机制（必须复用/遵守）**：
- `store.py:196-200` 自动命名只在 `title == "新会话"` 时覆盖——自定义改名后天然不被覆盖，**不要**为排除覆盖引入新标记字段；
- 归档搜索、归档详情接口不改。

**数据模型**：无 schema 变更（复用现有 title 列）。

**非目标**：不做批量重命名、不改自动命名算法、不做拖拽排序。

**验收**：
- 后端新增 `tests/test_session_rename.py`：current 与归档两路径改名成功、空标题 422/400、未知 id 404、改名后自动命名不覆盖；
- 前端新增 `frontend/src/components/__tests__/SessionList.rename.test.ts`：入口渲染、编辑后调用 API、失败提示可见。

**验收命令**：
```
VERIFY: .venv/Scripts/python -m pytest tests/test_session_rename.py -q ;; cd frontend && npx vitest run src/components/__tests__/SessionList.rename.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：2~3h。

---

## NP-04 欢迎卡导流重排 + 初见预期管理

**背景**：欢迎卡三个 chips（`ChatView.vue:717-719`，现为 查天气/画张图/聊菟丝子）没有一个指向产品最大差异点（记忆）和最好用的新手工具（能力演示，触发词「新手教程」）；新玩家也无从知道"菟菚慢热"。已核实 chips 就是 `input = '...'` 草稿预填模式，扩展成本极低。

**涉及文件**（修改）：
- `frontend/src/components/ChatView.vue`（欢迎卡区块，约 717 行附近）

**技术路线**：
1. chips 重排为三个，保持既有预填交互（点击填入输入框，不自动发送）：
   - 「你会什么？」→ `input = '新手教程'`（触发 `skills/agent-tour.md` 注入的 18 步真实演示，这是最强新手动线）；
   - 「帮我记住一件事」→ `input = '记住：我喜欢'`（用户补完后发送，第一次"她真的记得"的心动时刻）；
   - 「查天气」→ 保留现状；
   - 「画张图」「聊菟丝子」从欢迎卡移除（能力演示里都有，卡片保持三键不拥挤）；
2. 欢迎卡副标题下加一行小字（预期管理，不改人设）：`菟菚慢热，你说过的话她都记着——关系是攒出来的。`；
3. 卡片角落加次级入口文字链：`想自己逛 → 更多 → 能力演示`（仅文案，不改"更多"面板结构）。

**非目标**：不自动发送任何消息、不改 TourPanel 与 demo_tour.py、不动后端。

**验收**：
- 新增 `frontend/src/components/__tests__/ChatView.welcome.test.ts`（参考同目录 `TourPanel.test.ts` 的 mock 方式）：三个 chips 渲染、点击后 input 预填值正确、预期管理文案存在。

**验收命令**：
```
VERIFY: cd frontend && npx vitest run src/components/__tests__/ChatView.welcome.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：1~2h。

---

## NP-05 黑话翻译层（纯文案，不动逻辑）

**背景**：UI 直接暴露工程术语，新玩家每见一个词交一次理解税。已核实实例：主界面左下「归档 · 本机 · SQLite」（截图）、顶栏 MCP 图标全靠 title 辨识（`App.vue:407-476`）。

**涉及文件**（修改，均为文案与 title/aria 文本）：
- `frontend/src/components/SessionList.vue`：底部状态串「归档 · 本机 · SQLite」→「聊天记录只存在你自己的电脑上」；
- `frontend/src/App.vue`：顶栏 MCP 图标 title →「外挂工具（MCP）：接浏览器自动化这类外部能力，不用可以不管」；其余 12 个图标 title 逐个补足一句人话（至少说明"点了会发生什么"）；
- `frontend/src/components/ToolBar.vue`：MCP chip 的可见 label 不变，title 补同款人话；
- `frontend/src/components/SettingsPanel.vue`：「MCP 服务器」小节加副标题「接外部工具（进阶功能，可以完全不管）」；「语义检索」开关文案补括注「（换了说法也能想起来）」。实现时 grep 以下词并按同原则处理：`SQLite`、`MCP`、`SSE`、`声纹`、`embedding`、`stdio`。

**文案原则**：术语保留（老玩家要搜），但**首次出现处必须带人话括注**；不删功能、不改任何逻辑与样式结构。

**非目标**：不做文案系统/i18n 抽取、不改"她的抽屉"等拟人命名（那是独立的信息架构决策）。

**验收**：
- 新增 `frontend/src/components/__tests__/jargon.test.ts`：对 `SessionList.vue` 等做源码级断言（读文件内容断言不含裸「SQLite」、每个 `MCP` 的 title/邻接文本含「外挂」字样）——源码断言即可，不必渲染。

**验收命令**：
```
VERIFY: cd frontend && npx vitest run src/components/__tests__/jargon.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：1~2h。

---

## NP-06 首次运行向导（含宠物开关状态查询修复）

**背景**：前端**没有任何首次运行引导**（grep first_run/onboard/首次 为空）；新玩家的 API Key 要在记事本里填。已核实：`GET/POST /api/config` 齐备（`config_api.py:51,82`）；`pet:toggle` IPC 本就返回真实状态（`preload.ts:47`）但 `SettingsPanel.vue:295` 的 `petOn` 恒初始化 false 且从不同步——宠物实际开着时开关显示关闭，再点会误关。

**涉及文件**：
- 新建 `frontend/src/components/FirstRunWizard.vue`；
- 修改 `frontend/src/App.vue`（挂载向导 + 触发判断）；
- 修改 `frontend/electron/main.ts` + `frontend/electron/preload.ts`（新增 `pet:get-state` IPC）；
- 修改 `frontend/src/components/SettingsPanel.vue`（petOn 回显修复，向导与设置页共用同一查询）。

**技术路线**：
1. **触发**：`App.vue` onMounted 时，`localStorage['tuzhan.firstrun.v1']` 不存在 **且** `GET /api/config` 显示 `LLM_API_KEY` 为空 → 显示向导（遮罩模态，可跳过，跳过也落 flag）。已配好 Key 的老用户升级后不弹。
2. **步骤①欢迎**：三句话——她是谁（一句）、她慢热关系是攒出来的（一句）、所有记录只存在这台电脑上（一句）。
3. **步骤②填 Key**：base_url / model / key 三字段，默认值与 `SettingsPanel.vue` 现有一致；保存**复用** `SettingsPanel` 对 `POST /api/config` 的现有调用参数与成功/失败处理；保存后 `GET /api/config` 回读非空判定成功；失败给可见错误，不静默。不做"测试连接"在线校验（非目标）。
4. **步骤③桌面宠物**：仅桌面壳显示（桌面壳判定：实现时定位现有桌面版专属按钮的判定方式复用；若无现成 util，**自行补充** `isDesktopShell(): boolean`——以 `window.tuzhanPet`/`window.tuzhanStt` 等 preload 暴露对象存在为准，回信标注）。开关初始值来自新 IPC：
   - `main.ts`：`ipcMain.handle('pet:get-state', ...)` 返回宠物窗当前可见状态（`petWindow.ts` 若无状态 getter 则导出一个，只读不重建窗口）；
   - `preload.ts`：`getPetState: () => ipcRenderer.invoke('pet:get-state')`；
   - `SettingsPanel.vue:295`：`petOn` 初始化改为 onMounted 时 `await window.tuzhanPet.getPetState()`（网页版下隐藏该开关，维持现状）。
5. **步骤④完成**：写 flag；输入框预填 `新手教程` 并聚焦（**不自动发送**）。

**非目标**：不做账号/遥测、不做个性化问答、不改后端 config API、不做安装包打包变更。

**验收**：
- 新增 `frontend/src/components/__tests__/FirstRunWizard.test.ts`：无 flag 且 key 为空时出现、填 key 保存调用 config API、跳过写 flag、桌面态宠物步骤用 getPetState 初始化；
- `SettingsPanel` petOn 回显有断言；
- 手测：Electron 壳内走完四步；浏览器形态不显示宠物步骤。

**验收命令**：
```
VERIFY: cd frontend && npx vitest run src/components/__tests__/FirstRunWizard.test.ts ;; cd frontend && npx vue-tsc --noEmit
```
（Electron 外壳行为由 CI 的 `npm run test:electron` 兜底，不放进 VERIFY。）

预估：6~10h。

---

## 暂不派发清单（2026-09-27 拍板后七项全部解锁，任务书见文末「批次二」）

| 事项 | 留置原因 |
|---|---|
| 花费人话化（≈元/天估算） | 需拍板默认价目（模型价目易变，写死会错）——待用户给默认价或确认"用户自填单价"方案 |
| 消息编辑/重发 | 涉及会话存储的写路径与重发语义（上下文重算？截断？），需交互设计定稿 |
| 错误提示三套风格统一 | 需先定文案规范与"人设化报错"尺度，属规范类决策 |
| 托盘/置顶/自启/全局召唤键 | 桌面壳批量改动，值得独立成批次（可包含迷你速聊窗设计），本批不混入 |
| 数据目录迁移/自动更新 | 架构决策（%APPDATA% 迁移有兼容风险），需 ADR |
| 人格包格式（卡+立绘+音色） | 格式规范设计 + 美术资产规范前置 |
| 桌面感知（前台窗口/AFK） | 隐私红线设计需用户拍板（信号粒度、开关位置、话术披露） |

## 派发/执行说明

- 默认执行方为 ZCode（功能实现承接方）；若派发给 Codex worker：任务文本 = 本文件对应小节全文 + 全局禁区 + `VERIFY:` 行，`mode=edit`，`VERIFY_TIMEOUT:` 建议 600（NP-06 的 vue-tsc 较慢）。
- 验收：Codex 独立审查 diff + 复跑 VERIFY；FAIL 时任务照常勾选但备注注明，由派发方审查。
- 每个任务完成即在本文档对应小节标注状态（`已完成 @ commit`），保持信箱与文档同步。

---

# 批次二：拍板解锁任务书（NP-07 ~ NP-14）

> 2026-09-27 用户逐项拍板后解锁。全局禁区、验收纪律与批次一相同。执行顺序见上文。

## 拍板结果（用户确认，2026-09-27）

| # | 事项 | 决定 |
|---|---|---|
| 1 | 花费估算 | 自填单价 + 预填 deepseek-chat 参考价 → NP-07 |
| 2 | 消息重发 | 最小切片：限最后一条（重新生成 + 编辑）→ NP-08 |
| 3 | 错误文案 | 分区对待：聊天流人设化、工程面板中性 → NP-09 |
| 4 | 桌面批次 | 四件套 + 迷你速聊窗 → 拆 NP-10 + NP-11 |
| 5 | 数据目录 | 不迁移 + 护栏加固 → NP-12 |
| 6 | 人格包 | 最小包：卡 + 立绘，音色沿用 voice 字段 → NP-13 |
| 7 | 桌面感知 | 类别粒度 + 默认关 → NP-14 |

---

## NP-07 花费人话化（自填单价 + 预填默认）

**目标**：用量账本显示「近 7 天 ≈¥X.XX（约 ¥X.XX/天）」，单价用户可改、永不写死。

**涉及文件**（修改）：
- 后端：`getUsageSummary`（`frontend/src/api/usage.ts` 的数据源路由，实现时定位）返回体加 `estimated_cost`；
- `backend/api/config_api.py`：新配置键 `LLM_PRICE_INPUT` / `LLM_PRICE_OUTPUT`（元/百万 token，**默认 2.0 / 8.0**，注释注明为 deepseek-chat 参考价、按实际供应商修改）；
- `frontend/src/components/SettingsPanel.vue`：对话模型区加两个单价数字输入框（带单位说明）；
- `frontend/src/components/UsagePanel.vue`：金额行 + tooltip 注明「按你设置的单价估算」。

**技术路线**：
1. usage 聚合处按渠道 token × 单价/1M 求和；若现有统计不区分输入/输出 token，按 3:1 经验比拆算并在 UI 标注「粗略估算」——**自行补充决策**，回信标注；
2. 单价读写复用 `POST/GET /api/config` 既有机制，不新建存储；
3. 金额格式：`< 0.01` 显示「<¥0.01」，避免一堆 0.00。

**非目标**：不做实时计费、不做预算熔断（路线图 D10 范畴）、不做历史账单回溯。

**验收**：`tests/test_usage_cost.py`（单价缺省、自定义单价、估算计算）；UsagePanel 金额行 vitest 断言。

**验收命令**：
```
VERIFY: .venv/Scripts/python -m pytest tests/test_usage_cost.py -q ;; cd frontend && npx vitest run src/components/__tests__/usageCost.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：3~4h。

---

## NP-08 消息重发最小切片（限最后一条）

**目标**：最后一条 bot 回复可「重新生成」；最后一条用户消息可「编辑后重发」。不做任意历史分支。

**涉及文件**（修改）：
- `backend/session/store.py`：新增 `_truncate_current_sync(keep_count: int)` + async 包装（重写 messages_json 保留前 keep_count 条；`keep_count < 0` 或越界返回失败）；
- `backend/api/sessions.py`：`POST /sessions/current/truncate`，body `{"keep_count": int}`；
- `backend/api/chat.py`：请求体加可选 `regenerate: bool`——为 true 时**不追加用户消息**、直接基于现有历史生成；校验历史末轮必须是 user，否则 400；
- `frontend/src/components/ChatView.vue` / `MessageBubble.vue`：最后一条 bot 气泡 hover 加「重新生成」；最后一条用户气泡 hover 加「编辑」（文本填回输入框 → truncate 删旧条 → 用户手动发送，走正常 chat 路径）。

**关键语义（拍板确认，回信复述）**：被截断消息此前已被感知层提取的记忆/事实**不回滚**——接受该副作用，v1 不做事实回滚。

**技术路线**：重新生成 = `truncate(len-1)` → `streamChat({regenerate: true})`；busy 期间两个按钮禁用；「本轮不留痕」临时模式下两个按钮隐藏（临时会话语义不一致，**自行补充决策**，回信标注）。

**非目标**：任意历史消息编辑、分支树、删除单条消息、事实回滚。

**验收**：`tests/test_session_truncate.py`（边界、proactive 消息共存、regenerate 校验）；前端按钮渲染与调用序列 vitest。

**验收命令**：
```
VERIFY: .venv/Scripts/python -m pytest tests/test_session_truncate.py -q ;; cd frontend && npx vitest run src/components/__tests__/messageActions.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：6~10h。

---

## NP-09 错误提示分区对待

**目标**：聊天流内错误保持人设化；工程面板改中性清晰文案；消灭 `window.alert/confirm` 与静默吞错。

**涉及文件**（修改）：
- 新建 `frontend/src/utils/notify.ts`（最小 toast；实现时先 grep 是否已有同类基建——MessageBubble 删除失败提示（P3-50）有先例可复用）；
- `frontend/src/components/SettingsPanel.vue`（约 9 处 `window.confirm` → 复用其自定义重置弹窗的组件模式；5 处空 catch 补可见提示）、`App.vue:81-114`（alert → notify/弹窗）；
- `frontend/src/components/DiaryPanel.vue:31`、`UsagePanel.vue:29` 等拟人报错 → 中性（「日记加载失败，稍后再试」），实现时 grep 全部面板按同原则处理。

**文案规则**：中性错误 =「做了什么 + 失败原因 + 怎么办」，三段可缺但不可全缺；聊天流内（ChatView/MessageBubble 域）不动。

**非目标**：不做文案 i18n 抽取、不统一聊天流内人设文案、不重设计弹窗视觉。

**验收**：源码级断言 `src/**` 无 `window.alert(`/`window.confirm(`、无空 catch（正则 `catch\s*(\([^)]*\))?\s*\{\s*\}` 无命中）。

**验收命令**：
```
VERIFY: cd frontend && npx vitest run src/components/__tests__/errStyle.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：4~6h。

---

## NP-10 桌面四件套（托盘 / 置顶 / 自启 / 全局热键）

**目标**：补齐「桌面助手」底线存在感。

**涉及文件**（修改）：
- `frontend/electron/main.ts`（托盘菜单扩充：显示主窗 / 桌面宠物开关（勾选态）/ 退出；`globalShortcut` 注册唤起主窗；`app.setLoginItemSettings({ openAsHidden: true })`；quit 时 `globalShortcut.unregisterAll()`）；
- `frontend/electron/preload.ts`：`setAlwaysOnTop` / `setLaunchAtLogin` / `getLoginStatus` 通道；
- 窗口偏好持久化：扩展 `userData/pet-prefs.json` 同目录新增 `ui-prefs.json`（`alwaysOnTop` / `hotkeyMain`，自启状态以 `app.getLoginItemSettings` 为准不落盘）；
- `frontend/src/components/SettingsPanel.vue`：新增「桌面」区（置顶开关、自启开关、热键下拉）。

**技术路线**：热键 v1 只给候选下拉（默认 `Alt+Shift+T`，备选 `Ctrl+Alt+Z` / `Alt+Shift+Q`），不做自由录制——**自行补充决策**。注意 `Ctrl+Shift+T` 已被应用内主题切换占用，不得入选。开机自启后 `wasOpenedAtLogin` 为真时不显示主窗、仅托盘驻留。网页形态下「桌面」区整体隐藏。

**非目标**：迷你速聊窗（NP-11）、托盘菜单里放面板快捷入口、热键自由录制。

**验收**：设置区渲染 vitest；手测清单（托盘三项、置顶跨窗生效、自启后仅托盘、热键唤起/再按不重复开窗）写入回信；`npm run test:electron` CI 兜底。

**验收命令**：
```
VERIFY: cd frontend && npx vitest run src/components/__tests__/desktopBasics.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：6~8h。

---

## NP-11 迷你速聊窗（全局热键 → 说完即走）

**目标**：任意应用内按热键唤出置顶小输入条，问题即问即答，自动收起。召唤成本的天花板解决案。

**涉及文件**：
- `frontend/electron/main.ts`：新建迷你窗（约 420×180，置顶、skipTaskbar、`showInactive` 不抢焦点，Esc/失焦隐藏；globalShortcut 默认 `Alt+Shift+Space`，失效率高则回退候选 `Alt+Shift+M`——**自行补充决策**）；多页构建入口注册；
- `frontend/vite.config.ts`：新增 minichat 入口；
- 新建 `frontend/minichat.html`、`frontend/src/mini-main.ts`、`frontend/src/components/MiniChat.vue`。

**关键技术路线**：
1. MiniChat **直连后端** `streamChat`（复用 `src/api/chat.ts`），会话用 current——消息由后端正常落库，主窗打开即见完整历史，**迷你窗自己不维护会话状态**；
2. 窗口结构：一行输入 + 紧凑流式回复区 + 停止按钮；不渲染立绘/工具进度详情（空间不够）；
3. 回答完成 3 秒后自动隐藏（可配置关闭）——**自行补充决策**；
4. 生成期间可再编辑（沿用 NP-02 语义）；mini 窗内不提供确认面板，`confirm_request` 事件显示一行「请在主窗口确认」并引导唤起主窗。

**非目标**：迷你窗内历史浏览、图片发送、多会话切换、TTS。

**验收**：MiniChat 组件 vitest（渲染、发送调用、confirm_request 引导文案）；手测：游戏外任意应用内热键问答一轮、主窗可见同条消息；`test:electron` CI 兜底。

**验收命令**：
```
VERIFY: cd frontend && npx vitest run src/components/__tests__/MiniChat.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：6~10h。

---

## NP-12 数据护栏（不迁移 + 加固）

**目标**：让「她的记忆能不能活过一次覆盖更新」从靠用户记性变成靠产品。

**涉及文件**（修改）：
- 后端：新增 `POST /api/backup/run`（手动触发一次既有每日备份——复用 `backend/maintenance/` 内现有备份入口，实现时定位 `backup_manifest.py` 的调用方）、`GET /api/backup/status`（最新备份时间 + 完整性，复用 `backup_manifest` / `encrypted_backup.py` 的 `verify_files` 校验链路）；
- `frontend/src/components/SettingsPanel.vue` 数据保护区：「立即备份」「恢复演习」两按钮——演习 = 拉 status 展示「最近备份 X 天前 · 校验通过/失败」，**超 3 天未备份黄色提醒**；
- `scripts/deploy_assets/Start-Tuzhan.bat`：启动时检测到包版本标记变化 → 自动复制 `data/` → `data-backup-<时间戳>/`（若 bat 实现复杂度过高，降级为 README 强提示——**自行补充决策**，回信标注）。

**非目标**：不做自动恢复、不做云同步、不迁移数据目录（拍板：保持便携性）。

**验收**：`tests/test_backup_endpoints.py`（临时数据目录隔离：run 产出新备份、status 返回正确年龄与校验态）；设置按钮 vitest。

**验收命令**：
```
VERIFY: .venv/Scripts/python -m pytest tests/test_backup_endpoints.py -q ;; cd frontend && npx vitest run src/components/__tests__/backupPanel.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：4~6h。

---

## NP-13 人格包 v1（卡 + 立绘）

**目标**：导入一个 zip = 人格卡 + 该人格自己的立绘，解决「换了人格还是菟菚的脸」。

**涉及文件**（修改）：
- `frontend/src/components/PersonaSwitcher.vue`：文件选择 accept 增加 `.zip`，导入文案说明包格式；
- 人格导入后端路由（`importPersona` 客户端对应处，实现时定位）：识别 zip → 校验根目录含 `persona.md` → 解压 `portraits/` 到 `data/personas/<slug>/`（与现有旧卡保存目录同域）→ 人格档案记录 `portrait_dir`；
- `backend/api/images.py`：立绘路由解析顺序改为 **当前激活人格的 portraits 目录 → 全局 assets 默认 → 现有占位**；
- `backend/core/persona.py`：人格加载/切换携带 `portrait_dir`（随人格保存机制持久化，实现时确认存储位置：kv 键或 profile 字段，**无独立表变更**）；
- `docs/`：人格卡格式说明补「人格包」一节。

**包格式契约（v1 冻结）**：zip 根 = `persona.md`（现有格式，`voice` 字段沿用 Edge TTS 音色名）+ `portraits/{low,plain,lazy,happy,excited}.png`（任意子集，缺档回退 `plain`，全缺回退全局默认）。不引入 front matter 新字段——**约定优于配置**。

**非目标**：音频/SoVITS 声纹入包（等声纹档案体系成熟）、主题色入包、在线分享渠道。

**验收**：`tests/test_persona_pack.py`（合法包导入、缺 md 拒绝、坏 zip 拒绝、立绘路由回退顺序、切换人格后立绘跟随）；PersonaSwitcher accept vitest。

**验收命令**：
```
VERIFY: .venv/Scripts/python -m pytest tests/test_persona_pack.py -q ;; cd frontend && npx vitest run src/components/__tests__/personaPack.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：6~10h。

---

## NP-14 桌面感知 v1（类别粒度 + 默认关）

**目标**：她能（在你授权后）感知「你在写代码/在浏览/在全屏/离开了」，主动性更懂分寸——红线：只看应用类别，不看窗口标题与内容。

**涉及文件**（修改）：
- `backend/core/desktop_probe.py`：`probe_foreground` 返回体扩展 `process_name`、`category`、`idle_seconds`（进程名用 ctypes `QueryFullProcessImageNameW`，空闲用 `GetLastInputInfo`——**均非钩子，不抓屏、不读窗口内容**，遵守既有三不红线）；
- **契约扩展声明**：现契约（技术指导 §16 L10：只返回全屏 + 所在显示器）扩展为「+ 前台进程名→类别 + 空闲秒数」，实现时同步更新 `docs/Zcode技术指导.md` §16 描述并在回信标注；
- `backend/api/desktop.py`：新增 `GET /api/desktop/foreground`——开关关闭时只返回 `{"enabled": false}`，不探测；
- 配置：`DESKTOP_AWARENESS`（默认 `0` 关闭）+ 可选类别映射覆盖（默认表内置，非目标不做编辑器）；
- `backend/core/initiative.py`：必要性门新增信号——类别 `fullscreen`/`game` 时主动消息静默或降频（与现有「今日已主动」仲裁并列）；
- `backend/core/pipeline.py`：开关开启时上下文注入一行「用户前台正在：<类别>（约 X 分钟）」；
- `frontend/src/components/SettingsPanel.vue`：一级开关 + 固定说明「只识别应用类别，不看窗口内容，随时可关」。

**类别映射（v1 保守裁剪，回信标注）**：`code`（Code/Cursor/JetBrains/WindowsTerminal 等）/ `browse`（chrome/msedge/firefox）/ `idle`（输入空闲 > 5min）/ `fullscreen`（复用现判定）/ `other`。**v1 不猜游戏**（全屏 + 非 code/browse 归 fullscreen，不标 game）——保守优先，宁可不聪明不可吓人。

**非目标**：窗口标题/内容、白名单编辑器、「歇会儿」主动台词（后续内容批次）、每次前台变化都上报（探测仅在主动性仲裁与生成时按需调用，不做常驻轮询进程）。

**验收**：`tests/test_desktop_awareness.py`（mock ctypes：类别分类、开关关闭时不探测不注入、fullscreen 门控生效）；设置开关 vitest。

**验收命令**：
```
VERIFY: .venv/Scripts/python -m pytest tests/test_desktop_awareness.py -q ;; cd frontend && npx vitest run src/components/__tests__/awarenessToggle.test.ts ;; cd frontend && npx vue-tsc --noEmit
```

预估：8~12h。
