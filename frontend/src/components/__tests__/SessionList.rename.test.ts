// NP-03 归档重命名回归：自动命名撞车（五条同名「聊聊菟丝子吧」）后的可导航性兜底。
// 覆盖：入口→编辑→保存链路、列表即时更新、空标题拦截、API 失败可见提示、Esc 取消。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const renameSessionMock = vi.hoisted(() =>
  vi.fn(async (_id: string, _title: string) => true),
)

vi.mock('../../api/sessions', () => ({
  CURRENT_SESSION_ID: 'current',
  listArchives: vi.fn(async () => [
    { id: 'a1', title: '聊聊菟丝子吧', created_at: 1, message_count: 3 },
    { id: 'a2', title: '聊聊菟丝子吧', created_at: 2, message_count: 20 },
  ]),
  getArchive: vi.fn(async () => null),
  searchArchives: vi.fn(async () => []),
  renameSession: renameSessionMock,
}))
vi.mock('../../api', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, json: async () => ({}) })),
  getApiUrl: (p: string) => p,
}))
vi.mock('../../utils/images', () => ({
  resolveImageSrc: (p: string) => p,
}))

import SessionList from '../SessionList.vue'
import { renameSession } from '../../api/sessions'

const renameSessionSpy = vi.mocked(renameSession)

function mountList() {
  return mount(SessionList, {
    props: { open: true, currentId: null, personaName: '菟菚' },
  })
}

describe('SessionList 归档重命名（NP-03）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    renameSessionMock.mockResolvedValue(true)
  })
  it('同名归档可重命名：入口→编辑→Enter 保存→列表即时更新', async () => {
    const wrapper = mountList()
    await flushPromises()
    const items = wrapper.findAll('.sitem')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('聊聊菟丝子吧')

    await items[0].get('[aria-label="重命名归档"]').trigger('click')
    const input = items[0].get('input.rename-input')
    expect((input.element as HTMLInputElement).value).toBe('聊聊菟丝子吧')
    await input.setValue('我们的第一次长聊')
    await input.trigger('keydown.enter')
    await flushPromises()

    expect(renameSession).toHaveBeenCalledWith('a1', '我们的第一次长聊')
    expect(wrapper.findAll('.sitem')[0].text()).toContain('我们的第一次长聊')
    // 保存成功后退出编辑态
    expect(wrapper.find('input.rename-input').exists()).toBe(false)
  })

  it('空标题不调用 API 并提示', async () => {
    const wrapper = mountList()
    await flushPromises()
    const items = wrapper.findAll('.sitem')
    await items[0].get('[aria-label="重命名归档"]').trigger('click')
    const input = items[0].get('input.rename-input')
    await input.setValue('   ')
    await input.trigger('keydown.enter')
    await flushPromises()
    expect(renameSession).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('标题不能为空')
  })

  it('API 失败：可见错误提示且不退出编辑态', async () => {
    renameSessionMock.mockRejectedValue(new Error('boom'))
    const wrapper = mountList()
    await flushPromises()
    const items = wrapper.findAll('.sitem')
    await items[0].get('[aria-label="重命名归档"]').trigger('click')
    const input = items[0].get('input.rename-input')
    await input.setValue('新标题')
    await input.trigger('keydown.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('保存失败')
    expect(wrapper.find('input.rename-input').exists()).toBe(true)
  })

  it('Esc 取消编辑不改标题', async () => {
    const wrapper = mountList()
    await flushPromises()
    const items = wrapper.findAll('.sitem')
    await items[0].get('[aria-label="重命名归档"]').trigger('click')
    const input = items[0].get('input.rename-input')
    await input.setValue('改了又改')
    await input.trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(renameSession).not.toHaveBeenCalled()
    expect(wrapper.findAll('.sitem')[0].text()).toContain('聊聊菟丝子吧')
  })
})
