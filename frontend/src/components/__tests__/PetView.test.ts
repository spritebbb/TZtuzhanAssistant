import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import PetView from '../PetView.vue'

const petCalls = {
  drag: vi.fn(),
  close: vi.fn(),
  toggleIgnoreMouse: vi.fn(),
}

beforeEach(() => {
  vi.clearAllMocks()
  ;(window as unknown as Record<string, unknown>).tuzhanPet = {
    drag: petCalls.drag,
    close: petCalls.close,
    toggleIgnoreMouse: petCalls.toggleIgnoreMouse,
  }
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({
      // /api/meta 真实契约：mood.value 数值 + visual_state 结构化对象（presence.py）
      mood: { value: 70, label: '开心', emoji: '😄' },
      visual_state: { persona_id: 'default', mood_label: '开心' },
      persona: '菟菚',
    }),
  })))
})

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).tuzhanPet
  vi.unstubAllGlobals()
})

describe('L10 PetView', () => {
  it('按 /api/meta 的 mood.value 映射五档立绘（visual_state 是对象不可直接用）', async () => {
    const wrapper = mount(PetView)
    await new Promise((r) => setTimeout(r, 0)) // 等 fetch 完成
    const img = wrapper.get('.pet-portrait')
    expect(img.attributes('src')).toContain('/persona/full/happy')
    expect(img.attributes('src')).not.toContain('/persona/full/calm') // calm 不在后端白名单
    expect(img.attributes('alt')).toContain('菟菚')
    wrapper.unmount() // 解除 window 级监听，避免泄漏到后续用例
  })

  it('挂载即开启整体穿透，窗口 mousemove 按命中切换（P1-9 点击黑洞）', async () => {
    const wrapper = mount(PetView)
    await new Promise((r) => setTimeout(r, 0))
    expect(petCalls.toggleIgnoreMouse).toHaveBeenCalledWith(true) // 默认穿透
    window.dispatchEvent(new MouseEvent('mousemove', { clientX: 500, clientY: 500 }))
    expect(petCalls.toggleIgnoreMouse).toHaveBeenLastCalledWith(true) // 立绘外保持穿透
    wrapper.unmount()
  })

  it('Escape 走桥关闭（IPC 白名单内的 pet:close）', async () => {
    const wrapper = mount(PetView)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await new Promise((r) => setTimeout(r, 0))
    expect(petCalls.close).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('指针拖动把位移增量交给桥（主进程负责 clamp）', async () => {
    const wrapper = mount(PetView)
    await wrapper.trigger('pointerdown', { clientX: 100, clientY: 100 })
    expect(petCalls.toggleIgnoreMouse).toHaveBeenCalledWith(false)
    await wrapper.trigger('pointermove', { clientX: 140, clientY: 118 })
    await wrapper.trigger('pointermove', { clientX: 150, clientY: 130 })
    expect(petCalls.drag).toHaveBeenCalledWith({ dx: 40, dy: 18 })
    expect(petCalls.drag).toHaveBeenCalledWith({ dx: 10, dy: 12 })
    await wrapper.trigger('pointerup')
    expect(petCalls.toggleIgnoreMouse).toHaveBeenLastCalledWith(true) // 松手交还穿透
    await wrapper.trigger('pointermove', { clientX: 500, clientY: 500 })
    expect(petCalls.drag).toHaveBeenCalledTimes(2) // 松开后不再拖
    wrapper.unmount()
  })

  it('无桥（PWA 预览）时显示提示且不注册拖动', async () => {
    delete (window as unknown as Record<string, unknown>).tuzhanPet
    const wrapper = mount(PetView)
    expect(wrapper.get('.pet-hint').text()).toContain('桌面版')
    await wrapper.trigger('pointerdown', { clientX: 0, clientY: 0 })
    await wrapper.trigger('pointermove', { clientX: 30, clientY: 30 })
    expect(petCalls.drag).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
