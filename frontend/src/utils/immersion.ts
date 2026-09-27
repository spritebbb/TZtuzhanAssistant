// 沉浸模式偏好：开启后日常界面隐藏数值层（心情数字/进度条），
// 只留叙事化表达；想看数字仍可进「成长总览」或消息的「她为什么这样说」。
// 存储与事件模式与 tts.ts 的自动朗读偏好一致。
const IMMERSION_KEY = 'tztuzhan-immersion'
export const IMMERSION_EVENT = 'tztuzhan:immersion-changed'

export function isImmersionMode(): boolean {
  try { return localStorage.getItem(IMMERSION_KEY) === '1' } catch { return false }
}

export function setImmersionMode(enabled: boolean): void {
  try { localStorage.setItem(IMMERSION_KEY, enabled ? '1' : '0') } catch { /* ignore */ }
  window.dispatchEvent(new CustomEvent(IMMERSION_EVENT, { detail: { enabled } }))
}
