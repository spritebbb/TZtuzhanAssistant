import { ensureBaseUrl, getApiUrl } from '../api'

const AUTO_PLAY_KEY = 'tztuzhan-tts-autoplay'
export const TTS_STATE_EVENT = 'tztuzhan:tts-state'

export type TtsStatus = 'idle' | 'loading' | 'playing' | 'error'
export interface TtsState {
  key: string
  status: TtsStatus
  error?: string
}

let activeAudio: HTMLAudioElement | null = null
let activeKey = ''
let generation = 0

function emitState(state: TtsState) {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent<TtsState>(TTS_STATE_EVENT, { detail: state }))
  }
}

function releaseAudio() {
  if (!activeAudio) return
  activeAudio.onplaying = null
  activeAudio.onended = null
  activeAudio.onerror = null
  activeAudio.pause()
  activeAudio.removeAttribute('src')
  activeAudio.load()
  activeAudio = null
}

export function normalizeTtsText(text: string): string {
  return (text || '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/```[\s\S]*?```/g, '代码内容')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^\s{0,3}[#>*+-]+\s?/gm, '')
    .replace(/[~*_]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 5000)
}

export function getTtsAutoPlay(): boolean {
  try { return localStorage.getItem(AUTO_PLAY_KEY) === '1' } catch { return false }
}

export function setTtsAutoPlay(enabled: boolean) {
  try { localStorage.setItem(AUTO_PLAY_KEY, enabled ? '1' : '0') } catch { /* ignore */ }
}

export function stopTts() {
  generation += 1
  const stoppedKey = activeKey
  releaseAudio()
  activeKey = ''
  if (stoppedKey) emitState({ key: stoppedKey, status: 'idle' })
}

export async function playTts(text: string, key: string): Promise<void> {
  const spoken = normalizeTtsText(text)
  if (!spoken || !key) return

  stopTts()
  const run = ++generation
  activeKey = key
  emitState({ key, status: 'loading' })
  try {
    await ensureBaseUrl()
    if (run !== generation) return
    const query = new URLSearchParams({ text: spoken })
    const audio = new Audio(getApiUrl(`/api/tts?${query.toString()}`, true))
    activeAudio = audio
    audio.onplaying = () => {
      if (run === generation) emitState({ key, status: 'playing' })
    }
    audio.onended = () => {
      if (run !== generation) return
      releaseAudio()
      activeKey = ''
      emitState({ key, status: 'idle' })
    }
    audio.onerror = () => {
      if (run !== generation) return
      releaseAudio()
      activeKey = ''
      emitState({ key, status: 'error', error: '语音合成失败' })
    }
    await audio.play()
  } catch (error) {
    if (run !== generation) return
    releaseAudio()
    activeKey = ''
    const message = error instanceof Error ? error.message : '语音播放失败'
    emitState({ key, status: 'error', error: message })
  }
}

export function autoPlayTts(text: string, key: string) {
  if (getTtsAutoPlay()) void playTts(text, key)
}

