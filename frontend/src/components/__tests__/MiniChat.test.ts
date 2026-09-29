// NP-11 迷你速聊窗回归：直连 streamChat（current 会话）、IME 守卫、
// busy 时禁止重复发送、confirm_request 窗内允许/拒绝（DF-7：原「去主窗口」
// 引导是断头路）、错误可见、停止。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.hoisted(() => vi.fn(async () => ({
  json: async () => ({ ok: true, allow: true }),
})))

const streamChatMock = vi.hoisted(() => vi.fn(async (
  _text: string,
  _sessionId: string | null,
  _signal: AbortSignal,
  cb: {
    onPiece?: (p: string) => void
    onDone?: (t: string) => void
    onConfirmRequest?: (req: unknown) => void
    onError?: (err: string) => void
  },
) => {
  cb.onPiece?.('回答内容')
  cb.onDone?.('回答内容')
}))

vi.mock('../../api', () => ({
  apiFetch: apiFetchMock,
}))
vi.mock('../../api/chat', () => ({
  streamChat: streamChatMock,
}))
vi.mock('../../api/sessions', () => ({
  CURRENT_SESSION_ID: 'current',
}))

import MiniChat from '../MiniChat.vue'

function mountMini() {
  return mount(MiniChat)
}

describe('MiniChat 迷你速聊窗（NP-11）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
  })
  vi.useRealTimers()

  it('Enter 发送：直连 streamChat 且会话为 current；流式片段上屏', async () => {
    const wrapper = mountMini()
    const input = wrapper.get('input[aria-label="快问快答"]')
    await input.setValue('今天天气怎么样')
    await input.trigger('keydown.enter')
    await flushPromises()

    expect(streamChatMock).toHaveBeenCalledTimes(1)
    expect(streamChatMock.mock.calls[0][0]).toBe('今天天气怎么样')
    expect(streamChatMock.mock.calls[0][1]).toBe('current')
    expect(wrapper.text()).toContain('回答内容')
    // 发送后输入清空
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('busy 期间 Enter 不重复发送；停止按钮调用 abort', async () => {
    let release!: () => void
    streamChatMock.mockImplementationOnce((_t, _s, signal, cb) => new Promise<void>((resolve) => {
      release = () => { cb.onDone?.(''); resolve() }
      // signal abort 由组件负责
      void signal
    }))
    const wrapper = mountMini()
    const input = wrapper.get('input[aria-label="快问快答"]')
    await input.setValue('长问题')
    await input.trigger('keydown.enter')
    // 未完成：busy 中（停止按钮可见），Enter 不重复发送
    expect(wrapper.find('button[aria-label="停止生成"]').exists()).toBe(true)
    await input.trigger('keydown.enter')
    expect(streamChatMock).toHaveBeenCalledTimes(1)

    await wrapper.get('button[aria-label="停止生成"]').trigger('click')
    release()
    await flushPromises()
    expect(wrapper.find('button[aria-label="停止生成"]').exists()).toBe(false)
  })

  it('IME 组合中的 Enter 不发送（NP-01 同款守卫）', async () => {
    const wrapper = mountMini()
    const input = wrapper.get('input[aria-label="快问快答"]')
    await input.setValue('拼音中')
    await input.trigger('keydown.enter', { isComposing: true })
    await flushPromises()
    expect(streamChatMock).not.toHaveBeenCalled()
  })

  it('confirm_request 窗内提供允许/拒绝并调 POST /api/confirm（DF-7）；错误信息可见', async () => {
    streamChatMock.mockImplementationOnce(async (_t, _s, _sig, cb) => {
      cb.onConfirmRequest?.({ request_id: 'req-1', tool: 'delete_file', message: '删除文件' })
      cb.onDone?.('')
    })
    const wrapper = mountMini()
    const input = wrapper.get('input[aria-label="快问快答"]')
    await input.setValue('删个文件')
    await input.trigger('keydown.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('需要你确认')
    const allow = wrapper.get('button.mini-btn.allow')
    wrapper.get('button.mini-btn.deny')
    await allow.trigger('click')
    await flushPromises()
    expect(apiFetchMock).toHaveBeenCalledTimes(1)
    const call = apiFetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(call[0]).toBe('/api/confirm')
    expect(call[1].method).toBe('POST')
    expect(String(call[1].body)).toContain('request_id=req-1')
    expect(String(call[1].body)).toContain('allow=true')
    expect(wrapper.text()).toContain('已允许')

    streamChatMock.mockRejectedValueOnce(new Error('HTTP 500'))
    await input.setValue('再来一次')
    await input.trigger('keydown.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('HTTP 500')
  })

  it('confirm_request 缺 request_id 时退回主窗口引导（不渲染按钮）', async () => {
    streamChatMock.mockImplementationOnce(async (_t, _s, _sig, cb) => {
      cb.onConfirmRequest?.({})
      cb.onDone?.('')
    })
    const wrapper = mountMini()
    const input = wrapper.get('input[aria-label="快问快答"]')
    await input.setValue('奇怪响应')
    await input.trigger('keydown.enter')
    await flushPromises()
    expect(wrapper.text()).toContain('主窗口')
    expect(wrapper.find('button.mini-btn.allow').exists()).toBe(false)
  })
})
