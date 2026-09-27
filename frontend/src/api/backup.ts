// NP-12 数据护栏：手动备份与恢复演习（状态校验）。
import { apiFetch } from './index'

export interface BackupStatus {
  ok: boolean
  has_backup: boolean
  encrypted_mode: boolean
  name?: string
  completed_at?: number
  age_days?: number | null
  verify?: 'pass' | 'fail' | 'skip'
  verify_error?: string
  file_count?: number
  hint?: string
}

export async function getBackupStatus(): Promise<BackupStatus> {
  const r = await apiFetch('/api/backup/status')
  const d = await r.json()
  if (!r.ok || !d.ok) throw new Error(d.error || '备份状态读取失败')
  return d as BackupStatus
}

export async function runBackupNow(): Promise<string> {
  const r = await apiFetch('/api/backup/run', { method: 'POST' })
  const d = await r.json()
  if (!r.ok || !d.ok) throw new Error(d.error || '备份失败')
  return String(d.name || '')
}
