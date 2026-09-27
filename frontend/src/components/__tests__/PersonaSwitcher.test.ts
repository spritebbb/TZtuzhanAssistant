// 通用人格卡生成器回归：入口→简报→生成→可编辑预览→保存并启用（走既有导入路径）。
// 覆盖：空简报不发请求、生成失败可见提示、用户手改预览后以改后内容入库。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const generatePersonaMock = vi.hoisted(() =>
  vi.fn(async (_brief: string) => ({ markdown: '# 原始卡', name: '橘猫程序员' })),
)
const convertStCardMock = vi.hoisted(() =>
  vi.fn(async (_file: File) => ({
    markdown: '# 星野 澜\n\n## 设定\n\n深夜电台的主持人。',
    name: '星野 澜',
    warnings: ['世界书 / lorebook 未迁移：世界书只是场景素材，如需要可作为知识库文档投喂'],
  })),
)
const importPersonaMock = vi.hoisted(() =>
  vi.fn(async (_file: File) => ({
    id: 'ju-mao', name: '橘猫程序员', subtitle: '', theme: 'dark' as const,
    voice: '', active: true, created_at: 1,
  })),
)

vi.mock('../../api/personas', () => ({
  listPersonas: vi.fn(async () => ({
    active: { id: 'default', name: '菟菚', subtitle: '', theme: 'dark', voice: '', active: true, created_at: 0 },
    personas: [{ id: 'default', name: '菟菚', subtitle: '', theme: 'dark', voice: '', active: true, created_at: 0 }],
  })),
  activatePersona: vi.fn(),
  updatePersona: vi.fn(),
  importPersona: importPersonaMock,
  generatePersona: generatePersonaMock,
  convertStCard: convertStCardMock,
}))
vi.mock('../../api', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, json: async () => ({}) })),
}))

import PersonaSwitcher from '../PersonaSwitcher.vue'
import { generatePersona, importPersona, convertStCard } from '../../api/personas'

const importPersonaSpy = vi.mocked(importPersona)

function mountPanel() {
  return mount(PersonaSwitcher, {
    props: { show: true },
    global: { stubs: { teleport: true } },
  })
}

describe('PersonaSwitcher AI 生成人格卡', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    generatePersonaMock.mockResolvedValue({ markdown: '# 原始卡', name: '橘猫程序员' })
    importPersonaMock.mockResolvedValue({
      id: 'ju-mao', name: '橘猫程序员', subtitle: '', theme: 'dark',
      voice: '', active: true, created_at: 1,
    })
  })

  it('空简报不触发请求', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('button.generator-toggle').trigger('click')
    await wrapper.get('button.gen-run').trigger('click')
    expect(generatePersona).not.toHaveBeenCalled()
  })

  it('简报→生成→预览可编辑→保存并启用走导入路径', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('button.generator-toggle').trigger('click')

    await wrapper.get('textarea.gen-brief').setValue('毒舌但心软的猫娘程序员')
    await wrapper.get('button.gen-run').trigger('click')
    await flushPromises()
    expect(generatePersona).toHaveBeenCalledWith('毒舌但心软的猫娘程序员')

    const preview = wrapper.get('textarea.card-preview')
    expect((preview.element as HTMLTextAreaElement).value).toBe('# 原始卡')
    // 用户对生成结果手改一处，保存的必须是改后内容
    await preview.setValue('# 改过的卡')
    await wrapper.get('button.gen-save').trigger('click')
    await flushPromises()

    expect(importPersona).toHaveBeenCalledTimes(1)
    const file = importPersonaMock.mock.calls[0][0] as File
    expect(file.name).toContain('.md')
    expect(await file.text()).toBe('# 改过的卡')
    // 导入成功 → 列表刷新 + 通知父层切换 + 面板收起
    expect(wrapper.emitted('switched')).toBeTruthy()
    expect(wrapper.find('textarea.card-preview').exists()).toBe(false)
  })

  it('生成失败时错误可见且不进入导入', async () => {
    generatePersonaMock.mockRejectedValue(new Error('生成失败：未配置 API Key'))
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.get('button.generator-toggle').trigger('click')
    await wrapper.get('textarea.gen-brief').setValue('猫娘')
    await wrapper.get('button.gen-run').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('未配置 API Key')
    expect(wrapper.find('textarea.card-preview').exists()).toBe(false)
    expect(importPersona).not.toHaveBeenCalled()
  })
})

describe('PersonaSwitcher 人格包导入（NP-13）', () => {
  it('文件选择器接受 .md 与 .zip（人格包）', () => {
    const wrapper = mountPanel()
    const accept = wrapper.get('input[type="file"]').attributes('accept') || ''
    expect(accept).toContain('.md')
    expect(accept).toContain('.zip')
  })

  it('导入按钮文案覆盖人格包', () => {
    const wrapper = mountPanel()
    expect(wrapper.get('button.import:not(.generator-toggle)').text()).toContain('人格包')
  })
})

describe('PersonaSwitcher ST 卡导入向导（NP）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('选择 ST 卡 → 转换预览 + 关系从零提示 + 确认后走导入', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    const stBtn = wrapper.findAll('button.import').find((b) => b.text().includes('SillyTavern'))!
    await stBtn.trigger('click')
    const input = wrapper.get('input[accept=".json,.png,application/json,image/png"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['{}'], 'card.json', { type: 'application/json' })] })
    await input.trigger('change')
    await flushPromises()

    expect(convertStCard).toHaveBeenCalledTimes(1)
    expect((wrapper.get('textarea.card-preview').element as HTMLTextAreaElement).value).toContain('星野 澜')
    expect(wrapper.text()).toContain('关系与记忆会从零开始')
    expect(wrapper.text()).toContain('世界书')

    await wrapper.get('button.gen-save').trigger('click')
    await flushPromises()
    expect(importPersonaSpy).toHaveBeenCalledTimes(1)
    const sent = importPersonaSpy.mock.calls[0][0] as File
    expect(sent.name.endsWith('.md')).toBe(true)
  })
})
