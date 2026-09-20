import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatInput from '../ChatInput.vue'

const sttBridge = {
  start: vi.fn(async () => ({ ok: true as const })),
  pushAudio: vi.fn(),
  stop: vi.fn(async () => ({ ok: true })),
  cancel: vi.fn(async () => ({ ok: true })),
  onEvent: vi.fn((_cb: (ev: { op: string; text?: string }) => void) => () => undefined),
}

describe('ChatInput privacy mode', () => {
  it('exposes a one-turn no-trace switch', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        input: '',
        ephemeral: false,
        busy: false,
        streaming: false,
      },
    })

    await wrapper.get('[title="快捷指令"]').trigger('click')
    const privacy = wrapper.get('.privacy-toggle')
    expect(privacy.attributes('aria-pressed')).toBe('false')
    await privacy.trigger('click')
    expect(wrapper.emitted('update:ephemeral')?.[0]).toEqual([true])
  })

  it('disables image upload while no-trace mode is active', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        input: '',
        ephemeral: true,
        busy: false,
        streaming: false,
      },
    })

    const imageButton = wrapper.findAll('.icon-btn')[1]
    expect(imageButton.attributes('disabled')).toBeDefined()
    await wrapper.get('[title="快捷指令"]').trigger('click')
    expect(wrapper.get('.privacy-toggle').text()).toContain('已开启')
  })
})

describe('ChatInput 本地语音输入（L09）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;(window as unknown as Record<string, unknown>).tuzhanStt = sttBridge
  })

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).tuzhanStt
  })

  it('PWA 无桥：麦克风按钮禁用并说明是桌面能力', () => {
    delete (window as unknown as Record<string, unknown>).tuzhanStt
    const wrapper = mount(ChatInput, {
      props: { input: '', ephemeral: false, busy: false, streaming: false },
    })
    const mic = wrapper.get('[aria-label="语音输入"]')
    expect(mic.attributes('disabled')).toBeDefined()
    expect(mic.attributes('title')).toContain('桌面版')
  })

  it('桌面有桥：麦克风权限失败不创建 worker 请求', async () => {
    // happy-dom 没有 navigator.mediaDevices：PcmRecorder.start 抛错 → 走拒绝分支
    const wrapper = mount(ChatInput, {
      props: { input: '', ephemeral: false, busy: false, streaming: false },
    })
    const mic = wrapper.get('[aria-label="语音输入"]')
    expect(mic.attributes('disabled')).toBeUndefined()
    await mic.trigger('click')
    await new Promise((r) => setTimeout(r, 0))
    expect(sttBridge.start).not.toHaveBeenCalled()
    expect(mic.attributes('title')).toContain('权限')
  })

  it('final 事件只回填草稿，不自动发送', async () => {
    let eventCb: ((ev: { op: string; text?: string }) => void) | null = null
    sttBridge.onEvent.mockImplementationOnce(
      (cb: (ev: { op: string; text?: string }) => void) => {
        eventCb = cb
        return () => undefined
      },
    )
    const wrapper = mount(ChatInput, {
      props: { input: '今天', ephemeral: false, busy: false, streaming: false },
    })
    expect(eventCb).toBeTruthy()
    eventCb!({ op: 'final', text: '天气不错' })
    await new Promise((r) => setTimeout(r, 0))
    expect(wrapper.emitted('send')).toBeUndefined()
    // 草稿回填：input 模型收到「今天 + 识别文本」
    expect(wrapper.emitted('update:input')?.at(-1)).toEqual(['今天天气不错'])
  })
})
