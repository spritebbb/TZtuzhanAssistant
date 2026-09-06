import { apiFetch } from './index'

export type FocusStatus = 'active' | 'paused' | 'completed' | 'cancelled'

export interface FocusSession {
  id: number
  kind: 'focus'
  title: string
  status: FocusStatus
  planned_minutes: number
  remaining_seconds: number
  elapsed_seconds: number
  ends_at: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

async function focusRequest(path: string, init?: RequestInit): Promise<FocusSession> {
  const response = await apiFetch(path, init)
  const data = await response.json()
  if (!response.ok || !data.ok || !data.focus) {
    throw new Error(data.error || '专注状态更新失败')
  }
  return data.focus as FocusSession
}

export async function getCurrentFocus(): Promise<{ focus: FocusSession | null; justFinished: boolean }> {
  const response = await apiFetch('/api/focus/current')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '专注状态读取失败')
  return { focus: (data.focus as FocusSession | null) ?? null, justFinished: Boolean(data.just_finished) }
}

export function startFocus(minutes: 25 | 50): Promise<FocusSession> {
  return focusRequest('/api/focus', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ minutes }),
  })
}

export function pauseFocus(activityId: number): Promise<FocusSession> {
  return focusRequest(`/api/focus/${activityId}/pause`, { method: 'POST' })
}

export function resumeFocus(activityId: number): Promise<FocusSession> {
  return focusRequest(`/api/focus/${activityId}/resume`, { method: 'POST' })
}

export function completeFocus(activityId: number): Promise<FocusSession> {
  return focusRequest(`/api/focus/${activityId}/complete`, { method: 'POST' })
}

export function cancelFocus(activityId: number): Promise<FocusSession> {
  return focusRequest(`/api/focus/${activityId}/cancel`, { method: 'POST' })
}

export function exportFocusUrl(activityId: number): string {
  return `/api/focus/${activityId}/export?format=md`
}
