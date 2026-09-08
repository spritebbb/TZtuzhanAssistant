import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteFact,
  deleteUserTerm,
  getFacts,
  getHerProfile,
  getInteractionStyle,
  resetInteractionStyle,
  updateFact,
  updateFactPinned,
  resolveFactConflict,
  updateFactSurfacePolicy,
  type FactItem,
} from '../../api/memory'
import MemoryPanel from '../MemoryPanel.vue'

vi.mock('../RoomPanel.vue', () => ({ default: { template: '<section aria-label="共同审美" />' } }))
vi.mock('../../api/memory', () => ({
  getFacts: vi.fn(),
  updateFact: vi.fn(),
  updateFactPinned: vi.fn(),
  deleteFact: vi.fn(),
  updateFactSurfacePolicy: vi.fn(),
  resolveFactConflict: vi.fn(),
  getHerProfile: vi.fn(),
  getInteractionStyle: vi.fn(),
  resetInteractionStyle: vi.fn(),
  deleteUserTerm: vi.fn(),
}))
vi.mock('../../api/relationship', () => ({
  exportRelationshipUrl: vi.fn(() => '/api/relationship/export'),
  previewRestore: vi.fn(),
  restoreRelationship: vi.fn(),
  previewSeal: vi.fn(),
  sealMemories: vi.fn(),
  sealFileName: vi.fn((at: string) => `sealing-${(at || '').slice(0, 10) || 'bundle'}.json`),
}))

const fact: FactItem = {
  id: 7,
  content: '用户喜欢下雨天',
  ts: '2026-09-04T12:00:00',
  source_type: 'conversation_inference',
  source_message_ids: '[11,12]',
  confidence: 0.9,
  verified_at: null,
  expires_at: null,
  pinned: 0,
  surface_policy: 'normal',
  status: 'active',
  conflicts_with_fact_id: null,
  conflicting_content: null,
}

describe('MemoryPanel', () => {
  beforeEach(() => {
    vi.mocked(getFacts).mockReset()
    vi.mocked(updateFact).mockReset()
    vi.mocked(updateFactPinned).mockReset()
    vi.mocked(deleteFact).mockReset()
    vi.mocked(updateFactSurfacePolicy).mockReset()
    vi.mocked(resolveFactConflict).mockReset()
    vi.mocked(getHerProfile).mockReset()
    vi.mocked(getInteractionStyle).mockReset()
    vi.mocked(resetInteractionStyle).mockReset()
    vi.mocked(deleteUserTerm).mockReset()
    vi.mocked(getFacts).mockResolvedValue([{ ...fact }])
    vi.mocked(updateFact).mockResolvedValue()
    vi.mocked(updateFactPinned).mockResolvedValue()
    vi.mocked(updateFactSurfacePolicy).mockResolvedValue()
    vi.mocked(resolveFactConflict).mockResolvedValue()
    vi.mocked(getHerProfile).mockResolvedValue([
      { key: 'likes', label: '她喜欢', items: ['熬夜、咖啡、冷笑话'] },
    ])
    vi.mocked(getInteractionStyle).mockResolvedValue({
      style: '喜欢短句、偶尔用省略号',
      terms: [{ id: 3, term: '菟丝子', category: 'slang', meaning: '我们的黑话', count: 2 }],
    })
    vi.mocked(resetInteractionStyle).mockResolvedValue()
    vi.mocked(deleteUserTerm).mockResolvedValue()
  })

  it('shows provenance and persists the proactive-surface preference', async () => {
    const wrapper = mount(MemoryPanel, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(wrapper.text()).toContain('对话提炼')
    expect(wrapper.text()).toContain('置信度 90%')
    const toggle = wrapper.get<HTMLInputElement>('.surface-toggle input')
    await toggle.setValue(true)
    await flushPromises()

    expect(updateFactSurfacePolicy).toHaveBeenCalledWith(7, 'do_not_proactively_surface')
    expect(toggle.element.checked).toBe(true)
  })

  it('persists edited fact content', async () => {
    const wrapper = mount(MemoryPanel, { props: { show: false } })
    await wrapper.setProps({ show: true })
    await flushPromises()
    await wrapper.get('.actions button').trigger('click')
    await wrapper.get('textarea').setValue('用户更喜欢阵雨')
    await wrapper.get('button.primary').trigger('click')
    await flushPromises()

    expect(updateFact).toHaveBeenCalledWith(7, '用户更喜欢阵雨')
    expect(wrapper.text()).toContain('用户更喜欢阵雨')
    expect(wrapper.text()).toContain('你已确认')
    expect(wrapper.text()).toContain('置信度 100%')
  })

  it('lets the user keep a fact beyond natural decay', async () => {
    const wrapper = mount(MemoryPanel, { props: { show: true } })
    await flushPromises()

    const toggle = wrapper.get<HTMLInputElement>('.pin-toggle input')
    await toggle.setValue(true)
    await flushPromises()

    expect(updateFactPinned).toHaveBeenCalledWith(7, true)
    expect(toggle.element.checked).toBe(true)
  })

  it('renders a pending conflict and lets the user choose the new fact', async () => {
    vi.mocked(getFacts)
      .mockResolvedValueOnce([{
        ...fact,
        id: 8,
        content: '用户住在武汉',
        status: 'pending_confirmation',
        conflicts_with_fact_id: 7,
        conflicting_content: '用户住在襄阳',
      }])
      .mockResolvedValueOnce([{ ...fact, id: 8, content: '用户住在武汉' }])
    const wrapper = mount(MemoryPanel, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(wrapper.text()).toContain('等你确认')
    expect(wrapper.text()).toContain('用户住在襄阳')
    expect(wrapper.text()).toContain('用户住在武汉')
    await wrapper.get('.conflict-actions button.primary').trigger('click')
    await flushPromises()

    expect(resolveFactConflict).toHaveBeenCalledWith(8, 'accept_new')
    expect(wrapper.text()).not.toContain('等你确认')
  })

  it('shows the backup tab with export link and preview-then-restore flow', async () => {
    const { previewRestore, restoreRelationship } = await import('../../api/relationship')
    vi.mocked(previewRestore).mockResolvedValue({
      ok: true,
      errors: [],
      counts: { facts: 2, activities: 1 },
      total: 3,
      kv_exported: 1,
      target_user_id: 'assistant-main-bak',
      source_user_id: 'assistant-main',
    })
    vi.mocked(restoreRelationship).mockResolvedValue({ total: 3 })
    const wrapper = mount(MemoryPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.findAll('.tab-row button')[2].trigger('click')
    expect(wrapper.find('a[href="/api/relationship/export"]').exists()).toBe(true)

    const bundle = { kind: 'tuzhan-relationship-bundle', data: {} }
    const file = new File([JSON.stringify(bundle)], 'backup.json', { type: 'application/json' })
    const input = wrapper.get<HTMLInputElement>('input[type="file"]').element
    Object.defineProperty(input, 'files', { value: [file] })
    await wrapper.get('input[type="file"]').trigger('change')
    await flushPromises()

    await wrapper.get('.restore-target input').setValue('assistant-main-bak')
    await wrapper.get('.restore-target input').trigger('change')
    await flushPromises()
    expect(previewRestore).toHaveBeenCalledWith(bundle, 'assistant-main-bak')
    expect(wrapper.text()).toContain('将写入 3 条记录')

    vi.stubGlobal('confirm', () => true)
    // M8 封存卡片也用 reset-btn；恢复按钮定位在「恢复」卡片内，避免选择器歧义
    const restoreCard = wrapper.findAll('article.profile-card')
      .find((card) => card.find('input[type="file"]').exists())!
    await restoreCard.get('.reset-btn').trigger('click')
    await flushPromises()
    vi.unstubAllGlobals()
    expect(restoreRelationship).toHaveBeenCalledWith(bundle, 'assistant-main-bak')
    expect(wrapper.text()).toContain('恢复完成：共写入 3 条记录')
  })

  it('shows her stable profile and resets auto-formed interaction preferences', async () => {
    vi.stubGlobal('confirm', () => true)
    const wrapper = mount(MemoryPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.get('.tab-row button:nth-child(2)').trigger('click')
    await flushPromises()

    expect(getHerProfile).toHaveBeenCalled()
    expect(wrapper.text()).toContain('她喜欢')
    expect(wrapper.text()).toContain('熬夜、咖啡、冷笑话')
    expect(wrapper.text()).toContain('喜欢短句、偶尔用省略号')
    expect(wrapper.text()).toContain('菟丝子')

    await wrapper.get('.reset-btn').trigger('click')
    await flushPromises()
    expect(resetInteractionStyle).toHaveBeenCalled()
    expect(wrapper.text()).toContain('还没形成')

    await wrapper.get('.term-del').trigger('click')
    await flushPromises()
    expect(deleteUserTerm).toHaveBeenCalledWith(3)
    vi.unstubAllGlobals()
  })
})
