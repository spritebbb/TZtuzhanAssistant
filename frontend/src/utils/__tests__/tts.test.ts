import { beforeEach, describe, expect, it, vi } from 'vitest'

const { ensureBaseUrl, getApiUrl } = vi.hoisted(() => ({
  ensureBaseUrl: vi.fn(async () => 'http://127.0.0.1:8801'),
  getApiUrl: vi.fn((path: string) => `http://127.0.0.1:8801${path}`),
}))

vi.mock('../../api', () => ({ ensureBaseUrl, getApiUrl }))

import {
  TTS_STATE_EVENT,
  getTtsAutoPlay,
  normalizeTtsText,
  playTts,
  setTtsAutoPlay,
  stopTts,
} from '../tts'

class FakeAudio {
  static instances: FakeAudio[] = []
  src: string
  onplaying: (() => void) | null = null
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  pause = vi.fn()
  load = vi.fn()
  removeAttribute = vi.fn()
  play = vi.fn(async () => { this.onplaying?.() })

  constructor(src: string) {
    this.src = src
    FakeAudio.instances.push(this)
  }
}

describe('TTS 前端服务', () => {
  beforeEach(() => {
    stopTts()
    localStorage.clear()
    FakeAudio.instances = []
    vi.stubGlobal('Audio', FakeAudio)
    ensureBaseUrl.mockClear()
    getApiUrl.mockClear()
  })

  it('把 markdown 清理成适合朗读的纯文本', () => {
    expect(normalizeTtsText('## 标题\n看[这里](https://x.test)和 `code` **重点**'))
      .toBe('标题 看这里和 code 重点')
    expect(normalizeTtsText('![图](x.png)')).toBe('')
  })

  it('持久化自动播放偏好', () => {
    expect(getTtsAutoPlay()).toBe(false)
    setTtsAutoPlay(true)
    expect(getTtsAutoPlay()).toBe(true)
    setTtsAutoPlay(false)
    expect(getTtsAutoPlay()).toBe(false)
  })

  it('使用后端 TTS 地址并广播播放状态', async () => {
    const states: string[] = []
    const listener = (event: Event) => states.push((event as CustomEvent).detail.status)
    window.addEventListener(TTS_STATE_EVENT, listener)
    await playTts('你好，菟菚', 'msg-1')
    expect(FakeAudio.instances).toHaveLength(1)
    expect(FakeAudio.instances[0].src).toContain('/api/tts?text=')
    expect(decodeURIComponent(FakeAudio.instances[0].src)).toContain('你好，菟菚')
    expect(states).toEqual(['loading', 'playing'])
    FakeAudio.instances[0].onended?.()
    expect(states.at(-1)).toBe('idle')
    window.removeEventListener(TTS_STATE_EVENT, listener)
  })

  it('开始新朗读时停止上一段音频', async () => {
    await playTts('第一句', 'msg-1')
    const first = FakeAudio.instances[0]
    await playTts('第二句', 'msg-2')
    expect(first.pause).toHaveBeenCalled()
    expect(FakeAudio.instances).toHaveLength(2)
  })
})
