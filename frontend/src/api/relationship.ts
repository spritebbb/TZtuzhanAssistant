import { apiFetch } from './index'

export interface BundlePreview {
  ok: boolean
  errors: string[]
  counts: Record<string, number>
  total: number
  kv_exported: number
  target_user_id: string
  source_user_id: string | null
}

export function exportRelationshipUrl(): string {
  return '/api/relationship/export'
}

// ---- M8 阶段封存与告别：选定范围导出纪念包 + 告别信（只导出不删除）----

export interface SealPreview {
  categories: string[]
  counts: Record<string, Record<string, number>>
  exported_at: string
}

export async function previewSeal(categories: string[]): Promise<SealPreview> {
  const response = await apiFetch('/api/sealing/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ categories, letter: false }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '封存预览失败')
  return {
    categories: Array.isArray(data.categories) ? data.categories : [],
    counts: data.counts ?? {},
    exported_at: String(data.exported_at ?? ''),
  }
}

export async function sealMemories(categories: string[], withLetter = true): Promise<Blob> {
  const response = await apiFetch('/api/sealing', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ categories, letter: withLetter }),
  })
  if (!response.ok) {
    let message = '封存没有成功'
    try {
      const data = await response.json()
      if (data?.error) message = String(data.error)
    } catch {
      // 二进制或空响应体时保留默认文案
    }
    throw new Error(message)
  }
  return await response.blob()
}

export function sealFileName(sealedAt: string): string {
  const day = (sealedAt || '').slice(0, 10) || 'bundle'
  return `sealing-${day}.json`
}

export async function previewRestore(bundle: unknown, targetUserId: string): Promise<BundlePreview> {
  const response = await apiFetch('/api/relationship/restore/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bundle, target_user_id: targetUserId }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '预览失败')
  return data.preview as BundlePreview
}

export async function restoreRelationship(
  bundle: unknown,
  targetUserId: string,
): Promise<{ total: number }> {
  const response = await apiFetch('/api/relationship/restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bundle, target_user_id: targetUserId, dry_run: false }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '恢复失败')
  return { total: data.total as number }
}
