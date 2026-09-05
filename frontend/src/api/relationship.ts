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
