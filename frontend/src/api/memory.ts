import { apiFetch } from './index'

export interface FactItem {
  id: number
  content: string
  ts: string
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
