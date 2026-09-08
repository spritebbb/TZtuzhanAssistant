import { flushPromises, shallowMount } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, expect, it, vi } from 'vitest'
import App from '../App.vue'

vi.mock('../api', () => ({
  ensureBaseUrl: vi.fn().mockResolvedValue('http://local'),
  apiFetch: vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === '/api/flags'
      ? { flags: { compact_ui_enabled: true } }
      : { visual_state: { persona_id: 'default', revision: 1, mood_label: '平静', bond_label: '熟悉', energy_band: 'high', activity_kind: 'reading', presence: 'home', quiet: false, reduced_motion: false, source_time: '2026-09-09' }, recent_events: [] },
  })),
}))
vi.mock('../api/sessions', () => ({ CURRENT_SESSION_ID: 'current', archiveCurrent: vi.fn(), resetUser: vi.fn() }))
vi.mock('../api/personas', () => ({
  listPersonas: vi.fn().mockResolvedValue({ active: { id: 'default', name: '菟菚', subtitle: '助手', theme: 'dark', voice: '', active: true, created_at: 0, motion_enabled: true }, personas: [] }),
  updatePersona: vi.fn(),
}))
vi.mock('../utils/focusMode', () => ({ useFocusMode: () => ({ current: ref(null), remainingSec: ref(0), paused: ref(false), start: vi.fn(), stop: vi.fn() }) }))

beforeEach(() => localStorage.clear())

it('keeps four primary header entries and moves relationship numbers out of chat', async () => {
  const wrapper = shallowMount(App)
  await flushPromises()
  expect(wrapper.find('.aff-bar').exists()).toBe(false)
  expect(wrapper.find('.compact-actions').exists()).toBe(true)
  expect(wrapper.findAll('.compact-actions button')).toHaveLength(2)
  expect(wrapper.text()).not.toContain('好感度')
  await wrapper.get('[title="更多功能"]').trigger('click')
  expect(wrapper.get('[aria-label="更多功能"]').text()).toContain('成长总览')
  expect(wrapper.get('[aria-label="更多功能"]').text()).toContain('设置')
  wrapper.unmount()
})
