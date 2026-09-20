/**
 * L09：Electron 主进程的本地 STT worker 宿主。
 *
 * 职责边界（技术指导 §16 L09）：
 * - 以固定参数启动隐藏 worker（python -m backend.local_stt.worker，windowsHide、
 *   无 shell）；renderer 不能传可执行路径、任意模型路径或 shell 命令；
 * - IPC 白名单只有 stt:start / stt:audio / stt:stop / stt:cancel；
 * - worker 的控制帧原样转发给 renderer（stt:event）；
 * - worker 退出对 renderer 报告，下一次 start 自动重启（不假装存活）。
 */
import { ChildProcess, spawn } from 'child_process'
import { ipcMain } from 'electron'
import type { BrowserWindow } from 'electron'

import { SttDecoder, encodeAudio, encodeControl } from '../src/utils/stt'

/** 与 backend/local_stt/worker.py 的 WORKER_MODELS 保持一致（两边都不接受任意路径）。 */
export const STT_MODELS = ['tiny', 'base', 'small'] as const

export interface SttHostDeps {
  pythonExe: () => string
  backendRoot: () => string
  modelsDir: () => string
  getWindow: () => BrowserWindow | null
}

export class SttHost {
  private proc: ChildProcess | null = null
  private decoder = new SttDecoder()
  private starting = false

  constructor(private readonly deps: SttHostDeps) {}

  attach(): void {
    ipcMain.handle('stt:start', (_e, opts: { language?: string; modelRef?: string }) => {
      const modelRef = String(opts?.modelRef || 'base')
      if (!(STT_MODELS as readonly string[]).includes(modelRef)) {
        return { ok: false, error: `模型只支持 ${STT_MODELS.join('/')}` }
      }
      const okStart = this.ensureWorker()
      if (!okStart) return { ok: false, error: '本地转写进程启动失败' }
      this.send(encodeControl({
        op: 'start',
        request_id: 'ui',
        language: String(opts?.language || 'zh'),
        model_ref: modelRef,
        format: 'pcm16',
      }))
      return { ok: true }
    })
    ipcMain.on('stt:audio', (_e, chunk: Uint8Array) => {
      if (chunk instanceof Uint8Array && this.proc?.stdin?.writable) {
        this.send(encodeAudio(chunk))
      }
    })
    ipcMain.handle('stt:stop', () => {
      this.send(encodeControl({ op: 'stop', request_id: 'ui' }))
      return { ok: true }
    })
    ipcMain.handle('stt:cancel', () => {
      this.send(encodeControl({ op: 'cancel', request_id: 'ui' }))
      return { ok: true }
    })
  }

  dispose(): void {
    this.killWorker()
  }

  private ensureWorker(): boolean {
    if (this.proc && this.proc.exitCode === null && !this.starting) return true
    this.killWorker()
    this.decoder = new SttDecoder()
    this.starting = true
    try {
      this.proc = spawn(
        this.deps.pythonExe(),
        ['-m', 'backend.local_stt.worker'],
        {
          cwd: this.deps.backendRoot(),
          env: { ...process.env, LOCAL_STT_MODELS_DIR: this.deps.modelsDir() },
          shell: false, // renderer 永远无法注入 shell 命令
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'ignore'],
        },
      )
    } catch {
      this.starting = false
      return false
    }
    this.proc.on('exit', () => {
      this.proc = null
      this.emit({ op: 'error', code: 'worker_exited', message: '本地转写进程已退出' })
    })
    this.proc.stdout?.on('data', (buf: Buffer) => {
      let frames
      try {
        frames = this.decoder.push(new Uint8Array(buf))
      } catch {
        this.killWorker()
        this.emit({ op: 'error', code: 'worker_exited', message: '转写进程协议错乱，已停止' })
        return
      }
      for (const f of frames) {
        if (f.kind === 'control') this.emit(f.msg)
      }
    })
    this.starting = false
    return true
  }

  private send(data: Uint8Array): void {
    const stdin = this.proc?.stdin
    if (!stdin?.writable) {
      this.emit({ op: 'error', code: 'worker_exited', message: '本地转写进程不可用' })
      return
    }
    stdin.write(data)
  }

  private emit(msg: Record<string, unknown>): void {
    const win = this.deps.getWindow()
    if (win && !win.isDestroyed()) win.webContents.send('stt:event', msg)
  }

  private killWorker(): void {
    if (this.proc) {
      try { this.proc.kill() } catch { /* 已退出 */ }
      this.proc = null
    }
  }
}
