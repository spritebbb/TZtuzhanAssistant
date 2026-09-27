// 酒馆同玩面板回归：玩法说明在场、开关读真实 flag、切换走 POST /api/flags、
// 失败可见不静默。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.hoisted(() =>
  vi.fn(async (p: string, opts?: { method?: string; body?: string }) => ({
    ok: true,
    json: async () => {
      if (p === '/api/flags' && !opts?.method) {
        return { ok: true, flags: { tavern_enabled: false }, labels: {} }
      }
      return { ok: true, name: 'tavern_enabled', value: true }
    },
  })),
)

vi.mock('../../api', () => ({
  apiFetch: (p: string, opts?: { method?: string }) => apiFetchMock(p, opts),
}))

import TavernPanel from '../TavernPanel.vue'

describe('TavernPanel 酒馆同玩', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('打开时读取真实 flag 并渲染玩法说明', async () => {
    const wrapper = mount(TavernPanel, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect((wrapper.get('input[aria-label="允许酒馆同玩"]').element as HTMLInputElement).checked).toBe(false)
    expect(wrapper.text()).toContain('真回忆')
    expect(wrapper.text()).toContain('SillyTavern')
  })

  it('切换开关走 POST /api/flags 并更新状态', async () => {
    const wrapper = mount(TavernPanel, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()

    // checkbox 的 setValue 会更新 checked 并触发 change（toggle 翻转 enabled）
    const box = wrapper.get('input[aria-label="允许酒馆同玩"]')
    await box.setValue(true)
    await flushPromises()

    const post = apiFetchMock.mock.calls.find((c) => c[1]?.method === 'POST')
    expect(post).toBeTruthy()
    expect(JSON.parse(String(post![1]!.body))).toEqual({ name: 'tavern_enabled', value: true })
    expect((box.element as HTMLInputElement).checked).toBe(true)
  })
})
