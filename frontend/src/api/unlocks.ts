import { apiFetch } from './index'

export interface UnlockSlot {
  key: string
  kind: 'stage' | 'bond' | 'easter'
  title: string
  status: 'delivered' | 'pending' | 'locked'
  delivered_at: string | null
  content: string | null
}

export async function getUnlocks(): Promise<UnlockSlot[]> {
  const response = await apiFetch('/api/unlocks')
  if (!response.ok) throw new Error('收集册读取失败')
  const data = await response.json()
  return Array.isArray(data.slots) ? data.slots : []
}
