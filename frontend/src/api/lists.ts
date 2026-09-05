import { apiFetch } from './index'

export interface ListItem {
  id: number
  title: string
  creator: string
  note: string
  added_by: 'user' | 'tuzhan'
  ts: string
}

export type ListKind = 'song' | 'book'

export interface SharedList {
  id: number
  kind: 'list'
  title: string
  status: 'active' | 'paused' | 'completed' | 'cancelled'
  list_kind: ListKind
  kind_label: string
  created_at: string
  updated_at: string
  completed_at: string | null
  items: ListItem[]
  compiled: string
}

async function listRequest(path: string, init?: RequestInit): Promise<SharedList> {
  const response = await apiFetch(path, init)
  const data = await response.json()
  if (!response.ok || !data.ok || !data.list) {
    throw new Error(data.error || '清单没有记上，再试一次')
  }
  return data.list as SharedList
}

export async function listLists(): Promise<SharedList[]> {
  const response = await apiFetch('/api/lists')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '清单读取失败')
  return Array.isArray(data.lists) ? data.lists : []
}

export function startList(title: string, listKind: ListKind): Promise<SharedList> {
  return listRequest('/api/lists', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, list_kind: listKind }),
  })
}

export function addListItem(
  activityId: number,
  item: { title: string; creator?: string; note?: string },
): Promise<SharedList> {
  return listRequest(`/api/lists/${activityId}/items`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...item, added_by: 'user' }),
  })
}

export function removeListItem(activityId: number, itemId: number): Promise<SharedList> {
  return listRequest(`/api/lists/${activityId}/items/${itemId}`, { method: 'DELETE' })
}

export function pauseList(activityId: number): Promise<SharedList> {
  return listRequest(`/api/lists/${activityId}/pause`, { method: 'POST' })
}

export function resumeList(activityId: number): Promise<SharedList> {
  return listRequest(`/api/lists/${activityId}/resume`, { method: 'POST' })
}

export function cancelList(activityId: number): Promise<SharedList> {
  return listRequest(`/api/lists/${activityId}/cancel`, { method: 'POST' })
}

export function completeList(activityId: number): Promise<SharedList> {
  return listRequest(`/api/lists/${activityId}/complete`, { method: 'POST' })
}

export function exportListUrl(activityId: number): string {
  return `/api/lists/${activityId}/export?format=md`
}
