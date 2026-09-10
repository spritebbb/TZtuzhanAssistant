import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AgentPanel from '../AgentPanel.vue'
import DiaryPanel from '../DiaryPanel.vue'
import SettingsPanel from '../SettingsPanel.vue'

vi.mock('../../api', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, json: async () => ({ ok: true }) })),
}))
vi.mock('../../api/diary', () => ({
  getDiaries: vi.fn(async () => []),
  getResearchReports: vi.fn(async () => []),
}))
vi.mock('../../api/unlocks', () => ({ getUnlocks: vi.fn(async () => []) }))
vi.mock('../../api/relationship', () => ({
  getRelationshipStyle: vi.fn(async () => ({
    style_ids: ['companion'], style_labels: ['默契陪伴'], forming: false,
    reasons: ['一起完成过几件事'], valid_until: '2026-10-01',
  })),
  getDomainTrust: vi.fn(async () => ([
    { domain: 'daily', label: '日常', value: 55, reasons: [{ event_id: 7, delta: 2, occurred_at: '2026-09-08T10:00:00' }] },
  ])),
}))
vi.mock('../../utils/tts', () => ({
  getTtsAutoPlay: vi.fn(() => false), setTtsAutoPlay: vi.fn(), stopTts: vi.fn(),
}))

afterEach(() => { document.body.innerHTML = '' })

describe('Q5 panel accessibility', () => {
  it('exposes settings and agent panels as named modal dialogs', () => {
    const settings = mount(SettingsPanel, { props: { show: true }, attachTo: document.body })
    expect(document.querySelector('[role="dialog"][aria-label="设置"]')).not.toBeNull()
    expect(document.querySelector('[aria-label="关闭设置"]')).not.toBeNull()
    for (const control of Array.from(document.querySelectorAll<HTMLInputElement | HTMLSelectElement>('.settings input:not([type="hidden"]), .settings select'))) {
      expect(control.getAttribute('aria-label') || control.getAttribute('placeholder')).toBeTruthy()
    }
    settings.unmount()

    const agent = mount(AgentPanel, { props: { show: true }, attachTo: document.body })
    expect(document.querySelector('[role="dialog"][aria-label="任务代理"]')).not.toBeNull()
    expect(document.querySelector('[aria-label="关闭任务代理"]')).not.toBeNull()
    agent.unmount()
  })

  it('renders relationship trust as short descriptions and at most two source dates', async () => {
    const wrapper = mount(DiaryPanel, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()
    await wrapper.get('nav button:last-child').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('默契陪伴')
    expect(wrapper.text()).toContain('正在变得稳定')
    expect(wrapper.text()).toContain('2026-09-08 的相处经历')
    expect(wrapper.find('.domain-bar').exists()).toBe(false)
    expect(wrapper.find('.domain-value').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('55')
  })
})
