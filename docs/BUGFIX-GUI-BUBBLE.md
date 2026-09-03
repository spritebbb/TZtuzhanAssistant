# Bugfix: 菟菚 GUI 气泡内容不显示

**时间**：2026-09-03  
**模块**：frontend / `MessageBubble.vue`  
**影响**：所有 bot 消息的气泡**内容被吞掉**，只显示头像（菟）；user 气泡内容正常。  
**严重度**：🔴 视觉功能完全失效（但用户能继续对话，只是不看到文字）

## 现象

截图中可以看到：
- 用户气泡（"帮我查一下今天襄阳的天气"）内容正常显示
- 助手气泡只剩孤零零的"菟"头像 + 空白气泡
- 任何 bot 回复都看不到文字

## 根因

`frontend/src/components/MessageBubble.vue` 第 55-63 行的 `highlighted` computed 把"搜索高亮"和"内容渲染"耦合到了**同一个 v-html 出口**：

```js
const highlighted = computed(() => {
  const q = props.searchQuery?.trim()
  if (!q || !props.message.content) return ''   // ← BUG：无搜索词时返回空串
  const md = renderMarkdown(props.message.content)
  return md
})
```

而模板第 89 行：
```html
<div v-if="message.role === 'bot' && message.content" class="md" v-html="highlighted"></div>
```

**`v-if` 通过**（`message.content` 存在），但 `v-html` 渲染的是 `''` —— 气泡里就只剩空白。

`user` 角色走的是第 97 行的 `{{ message.content }}`，所以 user 气泡正常。

## 为什么是 GUI 端而不是后端

- 后端 SSE 返回 piece + done 完整正确（端到端测试验证：piece 逐字返回、done 一次性给完整文本）
- 后端日志里所有 `200/304` 正常，资源加载无误
- 数据库里 bot 消息的 content 字段也完整

问题纯粹在前端**渲染逻辑**：computed 的"无搜索词 → 空串"等价于"无内容"。

## 修复

把 `highlighted` 重命名为 `renderedContent`，**始终**返回 `renderMarkdown(content)`（即无搜索词时也走 markdown 渲染，搜索高亮由父组件传的 `isMatch`/`isActiveMatch` 驱动**边框样式**高亮，避免在已渲染的 HTML 里注入 `<mark>` 破坏结构）。

```js
// 修复后
const renderedContent = computed(() => {
  return props.message.content ? renderMarkdown(props.message.content) : ''
})
```

```html
<!-- 修复后 -->
<div v-if="message.content" class="md" v-html="renderedContent"></div>
```

`searchQuery` prop 保留接收（父组件仍然在传），但前端不再用它做内容渲染开关。

## 验证

- `vue-tsc` 类型检查通过
- `vite build` 构建成功（新 hash：`index-ByFbbf7t.js` / `index-Cug3Nz7o.css`）
- 后端 serve 新资源返回 200
- 端到端 SSE 流程验证：piece 流式正常，done 一次性给完整文本

刷新 Electron 窗口即可看到修复后的气泡内容。

## 备注

- 这类 bug 在 V12 审查中**没有被发现**——因为代码看起来"逻辑通顺"，实际却把"渲染开关"和"高亮逻辑"混在一起。**总结**：computed 的语义要单一，不要让一个值兼任"是否渲染 + 渲染什么"两个职责。
- 同时建议 ChatView 里考虑：当前 `archive-hint` 提示条条件是 `messages.length >= 40`，截图里只有 1-2 条消息所以不显示，这是设计预期。但归档按钮的入口（header 右上角的 📦 图标）仍然在——用户可以手动归档。
