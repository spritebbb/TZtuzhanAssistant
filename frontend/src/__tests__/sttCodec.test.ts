import { describe, expect, it } from 'vitest'

import {
  type SttFrame,
  SttDecoder,
  desktopSttBridge,
  encodeAudio,
  encodeControl,
} from '../utils/stt'

function drain(decoder: SttDecoder, bytes: Uint8Array): SttFrame[] {
  return decoder.push(bytes)
}

describe('L09 帧编解码', () => {
  it('控制帧与音频帧 roundtrip', () => {
    const decoder = new SttDecoder()
    const control = encodeControl({ op: 'start', request_id: 'x', model_ref: 'base' })
    const frames = drain(decoder, control)
    expect(frames).toHaveLength(1)
    expect(frames[0].kind).toBe('control')
    if (frames[0].kind === 'control') expect(frames[0].msg.op).toBe('start')

    const audio = encodeAudio(new Uint8Array([1, 2, 3, 4, 5]))
    const audioFrames = drain(decoder, audio)
    expect(audioFrames[0].kind).toBe('audio')
    if (audioFrames[0].kind === 'audio') expect(Array.from(audioFrames[0].data)).toEqual([1, 2, 3, 4, 5])
  })

  it('跨切块正确拼接（半个帧头 + 分段帧体）', () => {
    const payload = encodeControl({ op: 'stop', request_id: 'y' })
    const decoder = new SttDecoder()
    expect(decoder.push(payload.slice(0, 3))).toHaveLength(0) // 半个帧头
    expect(decoder.push(payload.slice(3, 6))).toHaveLength(0) // 帧头完、帧体缺
    const frames = decoder.push(payload.slice(6))
    expect(frames).toHaveLength(1)
    if (frames[0].kind === 'control') expect(frames[0].msg.op).toBe('stop'
    )
  })

  it('一串多帧一次吐出', () => {
    const blob = new Uint8Array([
      ...encodeControl({ op: 'started', request_id: 'z' }),
      ...encodeAudio(new Uint8Array([9, 9])),
    ])
    const frames = drain(new SttDecoder(), blob)
    expect(frames.map((f) => f.kind)).toEqual(['control', 'audio'])
  })

  it('未知帧类型抛错（协议错乱由上层杀 worker）', () => {
    const decoder = new SttDecoder()
    expect(() => decoder.push(new Uint8Array([0x99, 0, 0, 0, 0]))).toThrow()
  })

  it('PWA 无桥时 desktopSttBridge 返回 null', () => {
    // happy-dom 环境没有 preload 注入
    expect(desktopSttBridge()).toBeNull()
  })
})
