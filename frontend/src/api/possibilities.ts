import { apiFetch } from './index'

export type PossibilityMode = 'dream' | 'parallel'

export interface PossibilityArtifact {
  id: number
  artifact_type: string
  source_type: string
  source_id: number
  title: string
  content: string
  version: number
  created_at: string
  updated_at: string
}

function parseItem(data: unknown): PossibilityArtifact {
  const item = (data ?? {}) as Record<string, unknown>
  return {
    id: Number(item.id),
    artifact_type: String(item.artifact_type ?? ''),
    source_type: String(item.source_type ?? 'fiction'),
    source_id: Number(item.source_id ?? 0),
    title: String(item.title ?? ''),
    content: String(item.content ?? ''),
    version: Number(item.version ?? 1),
    created_at: String(item.created_at ?? ''),
    updated_at: String(item.updated_at ?? ''),
  }
}

/** 轻量探针：feature flag 关闭或接口不可用时抛错，角落据此隐藏虚构创作区。 */
export async function probePossibilities(): Promise<void> {
  const response = await apiFetch('/api/possibilities')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '梦境与平行可能性未开启')
}

export async function generatePossibilityDraft(
  mode: PossibilityMode,
  title: string,
  premise: string,
): Promise<string> {
  const response = await apiFetch('/api/possibilities/draft', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, title, premise }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '草稿没有生成，稍后再试')
  return String(data.draft ?? '')
}

export async function collectPossibility(
  mode: PossibilityMode,
  title: string,
  content: string,
): Promise<PossibilityArtifact> {
  const response = await apiFetch('/api/possibilities/collect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, title, content }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok || !data.item) throw new Error(data.error || '收藏没有成功')
  return parseItem(data.item)
}

export async function deletePossibility(id: number): Promise<void> {
  const response = await apiFetch(`/api/possibilities/${id}`, { method: 'DELETE' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '删除没有成功')
}
