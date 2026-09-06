import { apiFetch } from './index'

export type ViewpointRole = 'user' | 'tuzhan' | 'shared'

export interface ActivityViewpoint {
  role: ViewpointRole
  position: number
  content: string
  ts: string
}

export interface ReadingActivity {
  id: number
  kind: 'reading'
  document_id: number
  title: string
  status: 'active' | 'paused' | 'completed' | 'cancelled'
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
  viewpoints: ActivityViewpoint[]
  summary: string
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

export function pauseReading(activityId: number): Promise<ReadingActivity> {
  return activityRequest(`/api/activities/${activityId}/pause`, { method: 'POST' })
}

export function cancelReading(activityId: number): Promise<ReadingActivity> {
  return activityRequest(`/api/activities/${activityId}/cancel`, { method: 'POST' })
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

export function saveReadingViewpoint(
  activityId: number,
  role: ViewpointRole,
  content: string,
): Promise<ReadingActivity> {
  return activityRequest(`/api/activities/${activityId}/viewpoint`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, content }),
  })
}

export function completeReading(activityId: number): Promise<ReadingActivity> {
  return activityRequest(`/api/activities/${activityId}/complete`, { method: 'POST' })
}

export async function proposeReadingQuestion(
  activityId: number,
  userViewpoint = '',
): Promise<string> {
  const response = await apiFetch(`/api/activities/${activityId}/question`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_viewpoint: userViewpoint }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok || typeof data.question !== 'string') {
    throw new Error(data.error || '这一段的问题暂时没想出来')
  }
  return data.question
}

export function exportReadingUrl(activityId: number): string {
  return `/api/activities/${activityId}/export?format=md`
}
