// P3-04 D/E：应用锁客户端（/api/lock*）
import { apiFetch } from './index'

export type LockState = 'inactive' | 'unlocked' | 'locked'

export interface LockStatus {
  ok: boolean
  state: LockState
  unlocked: boolean
  key_id: string
  locked_at: number | null
  slots: { initialized: boolean; local_slot: boolean; recovery_slot: boolean }
  data_encrypted: boolean
}

export async function getLockStatus(): Promise<LockStatus> {
  const response = await apiFetch('/api/lock')
  if (!response.ok) throw new Error('锁状态读取失败')
  return await response.json() as LockStatus
}

export async function lockNow(): Promise<void> {
  const response = await apiFetch('/api/lock', { method: 'POST' })
  if (!response.ok) throw new Error('锁定失败')
}

export async function unlockLocal(): Promise<void> {
  const response = await apiFetch('/api/lock/unlock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ method: 'local' }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({ error: '解锁失败' }))
    throw new Error(data.error || '解锁失败')
  }
}

export async function unlockRecovery(passphrase: string): Promise<void> {
  const response = await apiFetch('/api/lock/unlock', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ method: 'recovery', passphrase }),
  })
  if (response.status === 429) {
    const data = await response.json().catch(() => ({ error: '' }))
    throw new Error(data.error || '尝试过于频繁，请稍后再试')
  }
  if (!response.ok) {
    // 统一错误信息来自后端（口令错/槽不可用不区分）
    const data = await response.json().catch(() => ({ error: '解锁失败' }))
    throw new Error(data.error || '解锁失败')
  }
}

export async function initializeKeyslots(passphrase: string, passphraseRepeat: string): Promise<void> {
  const response = await apiFetch('/api/lock/initialize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ passphrase, passphrase_repeat: passphraseRepeat }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({ error: '初始化失败' }))
    throw new Error(data.error || '初始化失败')
  }
}
