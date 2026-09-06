import { apiFetch } from './index'

export interface FactItem {
  id: number
  content: string
  ts: string
  source_type: 'legacy' | 'conversation_inference' | 'user_correction' | string
  source_message_ids: string
  confidence: number
  verified_at: string | null
  expires_at: string | null
  pinned: number
  surface_policy: 'normal' | 'do_not_proactively_surface' | 'never_surface'
  status: 'active' | 'pending_confirmation'
  conflicts_with_fact_id: number | null
  conflicting_content: string | null
}

export async function getFacts(limit = 200): Promise<FactItem[]> {
  const response = await apiFetch(`/api/memory/facts?limit=${limit}`)
  if (!response.ok) throw new Error('记忆读取失败')
  const data = await response.json()
  return Array.isArray(data.facts) ? data.facts : []
}

export async function updateFact(id: number, content: string): Promise<void> {
  const response = await apiFetch(`/api/memory/facts/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!response.ok) throw new Error('改写失败')
}

export async function deleteFact(id: number): Promise<void> {
  const response = await apiFetch(`/api/memory/facts/${id}`, { method: 'DELETE' })
  if (!response.ok) throw new Error('删除失败')
}

export async function updateFactSurfacePolicy(
  id: number,
  surfacePolicy: 'normal' | 'do_not_proactively_surface',
): Promise<void> {
  const response = await apiFetch(`/api/memory/facts/${id}/surface-policy`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ surface_policy: surfacePolicy }),
  })
  if (!response.ok) throw new Error('呈现策略更新失败')
}

export async function updateFactPinned(id: number, pinned: boolean): Promise<void> {
  const response = await apiFetch(`/api/memory/facts/${id}/pinned`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pinned }),
  })
  if (!response.ok) throw new Error('固定设置更新失败')
}

export async function resolveFactConflict(
  id: number,
  action: 'accept_new' | 'keep_existing',
): Promise<void> {
  const response = await apiFetch(`/api/memory/facts/${id}/resolve-conflict`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
  if (!response.ok) throw new Error('冲突记忆确认失败')
}

export interface HerProfileSection {
  key: string
  label: string
  items: string[]
}

export async function getHerProfile(): Promise<HerProfileSection[]> {
  const response = await apiFetch('/api/memory/her-profile')
  if (!response.ok) throw new Error('她的侧面读取失败')
  const data = await response.json()
  return Array.isArray(data.sections) ? data.sections : []
}

export interface UserTerm {
  id: number
  term: string
  category: string
  meaning: string
  count: number
}

export interface InteractionStyle {
  style: string
  terms: UserTerm[]
}

export async function getInteractionStyle(): Promise<InteractionStyle> {
  const response = await apiFetch('/api/memory/interaction-style')
  if (!response.ok) throw new Error('互动偏好读取失败')
  const data = await response.json()
  return { style: String(data.style ?? ''), terms: Array.isArray(data.terms) ? data.terms : [] }
}

export async function resetInteractionStyle(): Promise<void> {
  const response = await apiFetch('/api/memory/interaction-style', { method: 'DELETE' })
  if (!response.ok) throw new Error('重置失败')
}

export async function deleteUserTerm(id: number): Promise<void> {
  const response = await apiFetch(`/api/memory/terms/${id}`, { method: 'DELETE' })
  if (!response.ok) throw new Error('删除失败')
}
