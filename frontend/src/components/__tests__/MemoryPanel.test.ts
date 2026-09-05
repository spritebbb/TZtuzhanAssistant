import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteFact,
  getFacts,
  updateFact,
  resolveFactConflict,
  updateFactSurfacePolicy,
  type FactItem,
} from '../../api/memory'
import MemoryPanel from '../MemoryPanel.vue'

vi.mock('../../api/memory', () => ({
  getFacts: vi.fn(),
  updateFact: vi.fn(),
  deleteFact: vi.fn(),
  updateFactSurfacePolicy: vi.fn(),
  resolveFactConflict: vi.fn(),
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
    vi.mocked(deleteFact).mockReset()
    vi.mocked(updateFactSurfacePolicy).mockReset()
    vi.mocked(resolveFactConflict).mockReset()
    vi.mocked(getFacts).mockResolvedValue([{ ...fact }])
    vi.mocked(updateFact).mockResolvedValue()
    vi.mocked(updateFactSurfacePolicy).mockResolvedValue()
    vi.mocked(resolveFactConflict).mockResolvedValue()
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
})
