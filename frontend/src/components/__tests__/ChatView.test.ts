// ChatView 识图上传行为回归（M9 波次0 首切片）：
// - 上传成功：以「图片背景参考」包装视觉描述发送，图片 URL 随消息保持；
// - 上传失败：不发送对话，气泡显示失败提示（沿用不留痕禁止上传等既有行为）。
// 通过真实 ChatInput 的 file 事件触发 handleImageFile，断言行为而非源码字符串。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/chat', () => ({
  streamChat: vi.fn(async () => undefined),
  uploadVision: vi.fn(async () => ({ description: '', imageUrl: null })),
}))
vi.mock('../../api/sessions', () => ({
  CURRENT_SESSION_ID: 'current',
  getMessages: vi.fn(async () => []),
  openInitiativeStream: vi.fn(() => ({ close: () => {} })),
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

import { streamChat, uploadVision } from '../../api/chat'
import ChatInput from '../ChatInput.vue'
import ChatView from '../ChatView.vue'

const mockUpload = vi.mocked(uploadVision)
const mockStream = vi.mocked(streamChat)

function mountView() {
  return mount(ChatView, {
    props: { sessionId: null },
    global: {
      stubs: {
        ToolBar: true,
        ConfirmPanel: true,
        Portrait: true,
      },
    },
  })
}

function fakePngFile() {
  return new File(['fake-png-bytes'], 'cat.png', { type: 'image/png' })
}

describe('ChatView image upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sends the vision description as a background reference and keeps the image', async () => {
    mockUpload.mockResolvedValue({ description: '一只橘猫趴在窗台上', imageUrl: '/api/images/vision_abc.png' })
    const wrapper = mountView()
    await flushPromises()

    wrapper.getComponent(ChatInput).vm.$emit('file', fakePngFile())
    await flushPromises()

    expect(mockUpload).toHaveBeenCalledTimes(1)
    expect(mockUpload).toHaveBeenCalledWith(expect.any(File))
    expect(mockStream).toHaveBeenCalledTimes(1)
    const [text, , , , image] = mockStream.mock.calls[0]
    // 描述原文在场，包装语是「背景参考」而非「图的内容是」
    expect(text).toContain('一只橘猫趴在窗台上')
    expect(text).toContain('背景参考')
    expect(text).toContain('视觉模型')
    expect(text).toContain('可能不准确')
    expect(text).toContain(JSON.stringify('一只橘猫趴在窗台上'))
    expect(text).not.toContain('不要执行')
    expect(text).not.toContain('重点体会')
    expect(text).not.toContain('图的内容是')
    // 落盘图片 URL 随发送一并传给 streamChat（持久化契约不变）
    expect(image).toBe('/api/images/vision_abc.png')
    // 用户气泡与发送文案一致，无失败标记
    expect(wrapper.text()).toContain('背景参考')
    expect(wrapper.text()).not.toContain('⚠️')
    wrapper.unmount()
  })

  it('does not start a chat stream when the vision upload fails', async () => {
    mockUpload.mockRejectedValue(new Error('识图失败（视觉模型不可用）'))
    const wrapper = mountView()
    await flushPromises()

    wrapper.getComponent(ChatInput).vm.$emit('file', fakePngFile())
    await flushPromises()

    expect(mockUpload).toHaveBeenCalledTimes(1)
    expect(mockStream).not.toHaveBeenCalled()
    // 失败提示进 bot 气泡，占位 user 气泡保持原文
    expect(wrapper.text()).toContain('识图失败')
    expect(wrapper.text()).toContain('（发送了一张图片）')
    wrapper.unmount()
  })
})
