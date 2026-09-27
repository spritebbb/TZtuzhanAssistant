// NP-08 重发切片回归：操作条只对最后一条非临时消息出现——
// bot 尾→「重新生成」（截断 + streamChat regenerate）；user 尾→「编辑重发」（回填输入框）。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const streamChatMock = vi.hoisted(() => vi.fn(async (
  _text: string,
  _sessionId: string | null,
  _signal: AbortSignal,
  _cb: Record<string, unknown>,
  _image?: string | null,
  _requestId?: string,
  _ephemeral?: boolean,
  _regenerate?: boolean,
) => undefined))
const truncateMock = vi.hoisted(() => vi.fn(async (keep: number) => 1))

vi.mock('../../api/chat', () => ({
  streamChat: streamChatMock,
  uploadVision: vi.fn(async () => ({ description: '', imageUrl: null })),
}))
vi.mock('../../api/sessions', () => ({
  CURRENT_SESSION_ID: 'current',
  getMessages: vi.fn(async () => []),
  openInitiativeStream: vi.fn(() => ({ close: () => {} })),
  truncateSession: (keep: number) => truncateMock(keep),
}))
vi.mock('../../api', () => ({
  ensureBaseUrl: vi.fn(async () => undefined),
  getApiUrl: (p: string) => p,
  apiFetch: vi.fn(async () => ({ json: async () => ({}) })),
}))
vi.mock('../../utils/tts', () => ({
  TTS_STATE_EVENT: 'tztuzhan:tts-state',
  autoPlayTts: vi.fn(),
  playTts: vi.fn(),
  stopTts: vi.fn(),
}))

import ChatView from '../ChatView.vue'
import type { Message } from '../../api/sessions'

describe('消息重发操作条（NP-08）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    truncateMock.mockResolvedValue(1)
  })

  it('bot 尾：显示「重新生成」，点击截断并带 regenerate 发送', async () => {
    // 动态设定历史：需要 getMessages 返回两条——直接改模块 mock
    const sessions = (await import('../../api/sessions')) as unknown as {
      getMessages: ReturnType<typeof vi.fn>
    }
    sessions.getMessages.mockResolvedValue([
      { role: 'user', content: '第一问', ts: 1 },
      { role: 'bot', content: '第一答', ts: 2 },
    ] as Message[])
    streamChatMock.mockImplementationOnce((async (_t: unknown, _s: unknown, _sig: unknown, cb: { onDone?: (t: string) => void }) => {
      cb.onDone?.('重新生成的回答')
    }) as typeof streamChatMock)

    const wrapper = mount(ChatView, {
      props: { sessionId: 'current' },
      global: { stubs: { ToolBar: true, ConfirmPanel: true, Portrait: true } },
    })
    await flushPromises()

    const btn = wrapper.get('button.msg-action')
    expect(btn.text()).toContain('重新生成')
    await btn.trigger('click')
    await flushPromises()

    expect(truncateMock).toHaveBeenCalledWith(1) // 只截掉 bot
    expect(streamChatMock).toHaveBeenCalledTimes(1)
    // regenerate 走第 8 参数
    expect(streamChatMock.mock.calls[0][7]).toBe(true)
    expect(wrapper.text()).toContain('重新生成的回答')
  })

  it('user 尾：显示「编辑重发」，点击回填输入框且不发送', async () => {
    const sessions = (await import('../../api/sessions')) as unknown as { getMessages: ReturnType<typeof vi.fn> }
    sessions.getMessages.mockResolvedValue([
      { role: 'user', content: '想说的话', ts: 3 },
    ] as Message[])

    const wrapper = mount(ChatView, {
      props: { sessionId: 'current' },
      global: { stubs: { ToolBar: true, ConfirmPanel: true, Portrait: true } },
    })
    await flushPromises()

    const btn = wrapper.get('button.msg-action')
    expect(btn.text()).toContain('编辑重发')
    await btn.trigger('click')
    await flushPromises()

    expect(truncateMock).toHaveBeenCalledWith(0) // 保留 0 条：删掉这条 user
    expect(streamChatMock).not.toHaveBeenCalled()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('想说的话')
  })

  it('临时消息不出操作条', async () => {
    const sessions = (await import('../../api/sessions')) as unknown as { getMessages: ReturnType<typeof vi.fn> }
    sessions.getMessages.mockResolvedValue([
      { role: 'user', content: '不留痕', ephemeral: true, ts: 4 },
      { role: 'bot', content: '好的', ephemeral: true, ts: 5 },
    ] as Message[])

    const wrapper = mount(ChatView, {
      props: { sessionId: 'current' },
      global: { stubs: { ToolBar: true, ConfirmPanel: true, Portrait: true } },
    })
    await flushPromises()

    expect(wrapper.find('button.msg-action').exists()).toBe(false)
  })
})
