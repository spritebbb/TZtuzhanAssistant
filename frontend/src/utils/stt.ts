/**
 * L09 本地 STT：帧编解码（与 backend/local_stt/worker.py 对齐）+ 桌面桥探测 + PCM 采集。
 *
 * 帧格式：[1 字节类型 'J'|'A'][4 字节小端长度][payload]
 * 'J' = JSON 控制消息；'A' = 原始音频字节（16kHz 单声道 PCM16）。
 */

export const FRAME_CONTROL = 0x4a // 'J'
export const FRAME_AUDIO = 0x41 // 'A'

export interface SttControlMessage {
  op: string
  request_id?: string
  language?: string
  model_ref?: string
  format?: string
  text?: string
  code?: string
  message?: string
}

export type SttFrame =
  | { kind: 'control'; msg: SttControlMessage }
  | { kind: 'audio'; data: Uint8Array }

function frame(type: number, payload: Uint8Array): Uint8Array {
  const out = new Uint8Array(5 + payload.length)
  out[0] = type
  new DataView(out.buffer).setUint32(1, payload.length, true)
  out.set(payload, 5)
  return out
}

export function encodeControl(msg: SttControlMessage): Uint8Array {
  return frame(FRAME_CONTROL, new TextEncoder().encode(JSON.stringify(msg)))
}

export function encodeAudio(data: Uint8Array): Uint8Array {
  return frame(FRAME_AUDIO, data)
}

/** 流式解码器：喂任意切块的字节，吐出完整帧（跨块帧会正确拼接）。 */
export class SttDecoder {
  private buf = new Uint8Array(0)

  push(bytes: Uint8Array): SttFrame[] {
    const merged = new Uint8Array(this.buf.length + bytes.length)
    merged.set(this.buf)
    merged.set(bytes, this.buf.length)
    this.buf = merged
    const frames: SttFrame[] = []
    while (this.buf.length >= 5) {
      const type = this.buf[0]
      const len = new DataView(this.buf.buffer, this.buf.byteOffset).getUint32(1, true)
      if (type !== FRAME_CONTROL && type !== FRAME_AUDIO) throw new Error(`未知帧类型: ${type}`)
      if (this.buf.length < 5 + len) break
      const payload = this.buf.slice(5, 5 + len)
      this.buf = this.buf.slice(5 + len)
      if (type === FRAME_AUDIO) frames.push({ kind: 'audio', data: payload })
      else {
        const msg = JSON.parse(new TextDecoder().decode(payload)) as SttControlMessage
        frames.push({ kind: 'control', msg })
      }
    }
    return frames
  }
}

/** 桌面 STT 桥（Electron preload 注入）；PWA/浏览器里为 null。 */
export interface DesktopSttBridge {
  start(opts: { language: string; modelRef: string }): Promise<{ ok: boolean; error?: string }>
  pushAudio(chunk: Uint8Array): void
  stop(): Promise<{ ok: boolean }>
  cancel(): Promise<{ ok: boolean }>
  onEvent(cb: (ev: SttControlMessage) => void): () => void
}

export function desktopSttBridge(): DesktopSttBridge | null {
  const bridge = (window as unknown as { tuzhanStt?: DesktopSttBridge }).tuzhanStt
  return bridge ?? null
}

/**
 * 麦克风 PCM 采集：AudioContext 直采 16kHz 单声道 Int16。
 * ScriptProcessorNode 虽已标记废弃，但 Electron/Chromium 稳定支持且无需工作线程，
 * 骨架阶段够用；生产可换 AudioWorklet。
 */
export class PcmRecorder {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: ScriptProcessorNode | null = null
  private src: MediaStreamAudioSourceNode | null = null

  async start(onChunk: (pcm: Uint8Array) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    })
    type AudioContextCtor = new (opts?: { sampleRate?: number }) => AudioContext
    const Ctor = (window.AudioContext ||
      (window as unknown as { webkitAudioContext: AudioContextCtor }).webkitAudioContext) as AudioContextCtor
    this.ctx = new Ctor({ sampleRate: 16000 })
    this.src = this.ctx.createMediaStreamSource(this.stream)
    this.node = this.ctx.createScriptProcessor(4096, 1, 1)
    this.node.onaudioprocess = (ev: AudioProcessingEvent) => {
      const f32 = ev.inputBuffer.getChannelData(0)
      const i16 = new Int16Array(f32.length)
      for (let i = 0; i < f32.length; i++) {
        const s = Math.max(-1, Math.min(1, f32[i]))
        i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff
      }
      onChunk(new Uint8Array(i16.buffer))
    }
    // ScriptProcessor 必须接 destination 才会触发 onaudioprocess（静音直连即可）
    this.src.connect(this.node)
    this.node.connect(this.ctx.destination)
  }

  async stop(): Promise<void> {
    if (this.node) this.node.onaudioprocess = null
    try { this.node?.disconnect() } catch { /* 已断开 */ }
    try { this.src?.disconnect() } catch { /* 已断开 */ }
    this.stream?.getTracks().forEach((t) => t.stop())
    try {
      await this.ctx?.close()
    } catch { /* 已关闭 */ }
    this.node = null
    this.src = null
    this.stream = null
    this.ctx = null
  }
}
