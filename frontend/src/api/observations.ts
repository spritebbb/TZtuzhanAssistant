import { apiFetch } from './index'

export interface ObservationEntry {
  id: number
  observed_at: string
  observer: 'user' | 'assistant' | string
  content: string
  source_type: string
  source_id: number | null
  confidence: number
}

export interface Observation {
  id: number
  title: string
  status: 'active' | 'paused' | 'completed' | 'cancelled' | string
  created_at: string
  updated_at: string
  entries?: ObservationEntry[]
  log?: string
}

export async function listObservations(): Promise<Observation[]> {
  const response = await apiFetch('/api/observations')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '观察日志读取失败')
  return Array.isArray(data.observations) ? data.observations : []
}

export async function startObservation(title: string): Promise<Observation> {
  const response = await apiFetch('/api/observations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '开始记录失败')
  return data.observation as Observation
}

export async function getObservation(id: number): Promise<Observation> {
  const response = await apiFetch(`/api/observations/${id}`)
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '这本日志不存在')
  return data.observation as Observation
}

export async function addObservationEntry(
  id: number, content: string, observer: 'user' | 'assistant' = 'user',
): Promise<Observation> {
  const response = await apiFetch(`/api/observations/${id}/entries`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, observer }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '记录失败')
  return data.observation as Observation
}

export async function completeObservation(id: number): Promise<Observation> {
  const response = await apiFetch(`/api/observations/${id}/complete`, { method: 'POST' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '收尾失败')
  return data.observation as Observation
}

export async function cancelObservation(id: number): Promise<Observation> {
  const response = await apiFetch(`/api/observations/${id}/cancel`, { method: 'POST' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '放下失败')
  return data.observation as Observation
}
