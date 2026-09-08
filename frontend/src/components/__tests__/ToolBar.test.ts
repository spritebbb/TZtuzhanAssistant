import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../../api'
import ToolBar from '../ToolBar.vue'

vi.mock('../../api', () => ({ apiFetch: vi.fn() }))

beforeEach(() => {
  vi.mocked(apiFetch).mockReset().mockImplementation(async path => {
    if (path === '/api/meta') return { ok: true, json: async () => ({ tools: { search: true, weather: true, image: true, vision: true, memory: true, mcp: true } }) } as Response
    if (path === '/api/flags') return { ok: true, json: async () => ({ flags: { compact_ui_enabled: true } }) } as Response
    return { ok: true, json: async () => ({ ok: true }) } as Response
  })
})

it('renders no status strip when every capability is healthy', async () => {
  const wrapper = mount(ToolBar)
  await flushPromises()
  expect(wrapper.find('.toolbar').exists()).toBe(false)
  wrapper.unmount()
})

it('shows only clickable failures and their explanation', async () => {
  vi.mocked(apiFetch).mockImplementation(async path => {
    if (path === '/api/meta') return { ok: true, json: async () => ({ tools: { search: false, weather: true, image: true, vision: true, memory: true, mcp: true } }) } as Response
    if (path === '/api/flags') return { ok: true, json: async () => ({ flags: { compact_ui_enabled: true } }) } as Response
    return { ok: true } as Response
  })
  const wrapper = mount(ToolBar)
  await flushPromises()
  expect(wrapper.findAll('.failure')).toHaveLength(1)
  expect(wrapper.text()).toContain('联网异常')
  await wrapper.get('.failure').trigger('click')
  expect(wrapper.text()).toContain('当前未配置或已关闭')
  wrapper.unmount()
})

it('restores the legacy status layout when compact UI is off', async () => {
  vi.mocked(apiFetch).mockImplementation(async path => {
    if (path === '/api/meta') return { ok: true, json: async () => ({ tools: { search: true } }) } as Response
    if (path === '/api/flags') return { ok: true, json: async () => ({ flags: { compact_ui_enabled: false } }) } as Response
    return { ok: true } as Response
  })
  const wrapper = mount(ToolBar)
  await flushPromises()
  expect(wrapper.text()).toContain('工具')
  expect(wrapper.text()).toContain('后端在线')
  wrapper.unmount()
})
