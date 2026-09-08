import { apiFetch } from './index'

export interface WritingTurn {
  id: number
  author: 'user' | 'tuzhan'
  content: string
  ts: string
}

export interface CoWriting {
  id: number
  kind: 'writing'
  title: string
  status: 'active' | 'paused' | 'completed' | 'cancelled'
  premise: string
  created_at: string
  updated_at: string
  completed_at: string | null
  turns: WritingTurn[]
  story: string
  subtype?: 'story' | 'world' | 'character' | string
  subtype_label?: string
  outline?: Record<string, string[]>
}

async function writingRequest(path: string, init?: RequestInit): Promise<CoWriting> {
  const response = await apiFetch(path, init)
  const data = await response.json()
  if (!response.ok || !data.ok || !data.writing) {
    throw new Error(data.error || '故事没有记上，再试一次')
  }
  return data.writing as CoWriting
}

export async function listWritings(): Promise<CoWriting[]> {
  const response = await apiFetch('/api/writings')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '故事列表读取失败')
  return Array.isArray(data.writings) ? data.writings : []
}

export function startWriting(title: string, premise: string,
                            subtype: 'story' | 'world' | 'character' = 'story'): Promise<CoWriting> {
  return writingRequest('/api/writings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, premise, subtype }),
  })
}

export function addWritingTurn(activityId: number, content: string): Promise<CoWriting> {
  return writingRequest(`/api/writings/${activityId}/turn`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export function requestTuzhanTurn(activityId: number): Promise<CoWriting> {
  return writingRequest(`/api/writings/${activityId}/tuzhan-turn`, { method: 'POST' })
}

export function pauseWriting(activityId: number): Promise<CoWriting> {
  return writingRequest(`/api/writings/${activityId}/pause`, { method: 'POST' })
}

export function resumeWriting(activityId: number): Promise<CoWriting> {
  return writingRequest(`/api/writings/${activityId}/resume`, { method: 'POST' })
}

export function cancelWriting(activityId: number): Promise<CoWriting> {
  return writingRequest(`/api/writings/${activityId}/cancel`, { method: 'POST' })
}

export function completeWriting(activityId: number, createArtifact: boolean): Promise<CoWriting> {
  return writingRequest(`/api/writings/${activityId}/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ create_artifact: createArtifact }),
  })
}

export function exportWritingUrl(activityId: number): string {
  return `/api/writings/${activityId}/export?format=md`
}

/** L02 结构化大纲（世界观/角色设定）。 */
export interface OutlineDraft {
  subtype: string
  fields: string[]
  draft: Record<string, string[]>
  version: number
}

export async function getOutlineDraft(activityId: number): Promise<OutlineDraft> {
  const response = await apiFetch(`/api/writings/${activityId}/outline-draft`, { method: 'POST' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '大纲草稿生成失败')
  return data as OutlineDraft
}

export async function confirmOutline(
  activityId: number, outline: Record<string, string[]>, expectedVersion: number,
): Promise<OutlineDraft> {
  const response = await apiFetch(`/api/writings/${activityId}/outline`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ outline, expected_version: expectedVersion }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '大纲保存失败')
  return data as OutlineDraft
}
