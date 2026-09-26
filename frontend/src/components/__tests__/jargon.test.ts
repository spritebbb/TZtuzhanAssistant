// NP-05 黑话翻译层：用户可见文案中工程术语不得裸奔（源码级断言，不渲染组件）。
// 原则：术语保留（便于老用户搜索），但出现处必须带人话括注。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const src = (p: string) => readFileSync(join(here, '..', p), 'utf-8')

describe('NP-05 黑话翻译', () => {
  it('SessionList 底部不再裸露 SQLite', () => {
    const s = src('SessionList.vue')
    expect(s).not.toContain('SQLite')
    expect(s).toContain('聊天记录只存在你自己的电脑上')
  })

  it('ToolBar 能力 chip 悬停带人话说明，MCP 有外挂工具括注', () => {
    const s = src('ToolBar.vue')
    expect(s).toContain('外挂工具（MCP）')
    expect(s).toContain("hints[name] || name")
  })

  it('SettingsPanel MCP 小节与语义检索有人话括注', () => {
    const s = src('SettingsPanel.vue')
    expect(s).toContain('外挂工具接口')
    expect(s).toContain('换了说法也能想起来')
  })

  it('App.vue 任务代理/用量账本标题去黑话', () => {
    const s = src('../App.vue')
    expect(s).toContain('就能派活')
    expect(s).not.toContain('token 用量')
  })
})
