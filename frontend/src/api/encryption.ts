// P3-04 E：启用加密 / 迁移状态 / 明文清理（/api/encryption*）
import { apiFetch } from './index'

export interface EncryptionStatus {
  ok: boolean
  data_encrypted: boolean
  slots_initialized: boolean
  journal_state: string | null
  plaintext_keep: string | null
  gate_engaged: boolean
}

export interface MigrationJournal {
  state: string
  plaintext_keep: string
  asset_count: number
  carried_over: string[]
}

export async function getEncryptionStatus(): Promise<EncryptionStatus> {
  const response = await apiFetch('/api/encryption/status')
  if (!response.ok) throw new Error('加密状态读取失败')
  return await response.json() as EncryptionStatus
}

export async function enableEncryption(passphrase?: string, passphraseRepeat?: string): Promise<MigrationJournal> {
  const response = await apiFetch('/api/encryption/enable', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ passphrase, passphrase_repeat: passphraseRepeat }),
  })
  const data = await response.json().catch(() => ({ error: '启用失败' }))
  if (!response.ok) throw new Error(data.error || '启用失败')
  return data.journal as MigrationJournal
}

export async function cleanupPlaintext(): Promise<void> {
  const response = await apiFetch('/api/encryption/cleanup', { method: 'POST' })
  if (!response.ok) {
    const data = await response.json().catch(() => ({ error: '清理失败' }))
    throw new Error(data.error || '清理失败')
  }
}
