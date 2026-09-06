import { apiFetch } from './index'

export interface DualPerspective {
  id: number
  title: string
  source_type: string
  source_id: number | null
  source_date: string
  source_label: string
  user_view: string
  tuzhan_view: string
  tuzhan_view_origin: 'llm' | 'user'
  created_at: string
  updated_at: string
}

export interface DualAnchorCandidates {
  events: { id: number; label: string }[]
  diary: { id: number; label: string }[]
  goals: { id: number; label: string }[]
  artifacts: { id: number; label: string }[]
}

function parseItem(data: unknown): DualPerspective {
  const item = (data ?? {}) as Record<string, unknown>
  return {
    id: Number(item.id),
    title: String(item.title ?? ''),
    source_type: String(item.source_type ?? 'free'),
    source_id: item.source_id == null ? null : Number(item.source_id),
    source_date: String(item.source_date ?? ''),
    source_label: String(item.source_label ?? ''),
    user_view: String(item.user_view ?? ''),
    tuzhan_view: String(item.tuzhan_view ?? ''),
    tuzhan_view_origin: item.tuzhan_view_origin === 'llm' ? 'llm' : 'user',
    created_at: String(item.created_at ?? ''),
    updated_at: String(item.updated_at ?? ''),
  }
}

export async function listDualPerspectives(): Promise<DualPerspective[]> {
  const response = await apiFetch('/api/dual-perspectives')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '双视角读取失败')
  return Array.isArray(data.items) ? data.items.map(parseItem) : []
}

export async function listDualAnchorCandidates(): Promise<DualAnchorCandidates> {
  const response = await apiFetch('/api/dual-perspectives/anchors')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '候选经历读取失败')
  const pick = (value: unknown) =>
    Array.isArray(value)
      ? value.map((item) => {
          const row = (item ?? {}) as Record<string, unknown>
          return { id: Number(row.id), label: String(row.label ?? '') }
        })
      : []
  return {
    events: pick(data.events),
    diary: pick(data.diary),
    goals: pick(data.goals),
    artifacts: pick(data.artifacts),
  }
}

export async function createDualPerspective(payload: {
  title: string
  source_type: string
  source_id?: number | null
  user_view?: string
  tuzhan_view?: string
}): Promise<DualPerspective> {
  const response = await apiFetch('/api/dual-perspectives', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const data = await response.json()
  if (!response.ok || !data.ok || !data.item) throw new Error(data.error || '这一页没有建成，再试一次')
  return parseItem(data.item)
}

export async function saveDualPerspectiveView(
  id: number,
  role: 'user' | 'tuzhan',
  content: string,
  origin: 'llm' | 'user' = 'user',
): Promise<DualPerspective> {
  const response = await apiFetch(`/api/dual-perspectives/${id}/view`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, content, origin }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok || !data.item) throw new Error(data.error || '保存没有成功')
  return parseItem(data.item)
}

export async function generateTuzhanDraft(id: number): Promise<string> {
  const response = await apiFetch(`/api/dual-perspectives/${id}/tuzhan-draft`, { method: 'POST' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '草稿没有生成，稍后再试或代她写下这一段')
  return String(data.draft ?? '')
}

export async function deleteDualPerspective(id: number): Promise<void> {
  const response = await apiFetch(`/api/dual-perspectives/${id}`, { method: 'DELETE' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '删除没有成功')
}
