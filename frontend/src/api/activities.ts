import { apiFetch } from './index'

export interface ReadingActivity {
  id: number
  kind: 'reading'
  document_id: number
  title: string
  status: 'active' | 'paused' | 'completed'
  position: number
  created_at: string
  updated_at: string
  completed_at: string | null
  filename: string
  format: string
  chunk_count: number
  total: number
  progress: number
  excerpt: string
  note: string
  note_count: number
}

async function activityRequest(path: string, init?: RequestInit): Promise<ReadingActivity> {
  const response = await apiFetch(path, init)
  const data = await response.json()
  if (!response.ok || !data.ok || !data.activity) {
    throw new Error(data.error || '共读记录更新失败')
  }
  return data.activity as ReadingActivity
}

export async function listReadingActivities(): Promise<ReadingActivity[]> {
  const response = await apiFetch('/api/activities')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '共读记录读取失败')
  return Array.isArray(data.activities) ? data.activities : []
}

export function startReading(documentId: number): Promise<ReadingActivity> {
  return activityRequest('/api/activities/reading', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_id: documentId }),
  })
}

export function resumeReading(activityId: number): Promise<ReadingActivity> {
  return activityRequest(`/api/activities/${activityId}/resume`, { method: 'POST' })
}

export function setReadingPosition(activityId: number, position: number): Promise<ReadingActivity> {
  return activityRequest(`/api/activities/${activityId}/position`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ position }),
  })
}

export function saveReadingNote(activityId: number, content: string): Promise<ReadingActivity> {
  return activityRequest(`/api/activities/${activityId}/note`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export function completeReading(activityId: number): Promise<ReadingActivity> {
  return activityRequest(`/api/activities/${activityId}/complete`, { method: 'POST' })
}
