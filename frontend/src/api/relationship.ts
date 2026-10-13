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

// ---- L03 长期关系气质 ----
export interface RelationshipStyle {
  style_ids: string[]
  style_labels: string[]
  forming: boolean
  reasons: string[]
  valid_until: string
}

export async function getRelationshipStyle(): Promise<RelationshipStyle> {
  const response = await apiFetch('/api/memory/relationship-style')
  if (!response.ok) throw new Error('关系气质读取失败')
  const data = await response.json()
  return {
    style_ids: data.style_ids ?? [],
    style_labels: data.style_labels ?? [],
    forming: Boolean(data.forming),
    reasons: data.reasons ?? [],
    valid_until: String(data.valid_until ?? ''),
  }
}

export async function blockRelationshipStyle(style: string): Promise<void> {
  const response = await apiFetch(`/api/memory/relationship-style/${style}/block`, { method: 'POST' })
  if (!response.ok) throw new Error('屏蔽失败')
}

export async function unblockRelationshipStyle(style: string): Promise<void> {
  const response = await apiFetch(`/api/memory/relationship-style/${style}/block`, { method: 'DELETE' })
  if (!response.ok) throw new Error('撤销失败')
}

export interface DomainTrustItem {
  domain: string
  label: string
  value: number
  reasons: { event_id: number; delta: number; occurred_at: string }[]
}

/** L05 五域信任：高层可依赖程度 + 每域最多 2 条来源（不返回内部权重）。 */
export async function getDomainTrust(): Promise<DomainTrustItem[]> {
  const response = await apiFetch('/api/memory/domain-trust')
  if (!response.ok) throw new Error('领域信任读取失败')
  const data = await response.json()
  return (data.domains ?? []).map((item: DomainTrustItem) => ({
    domain: String(item.domain),
    label: String(item.label),
    value: Number(item.value ?? 0),
    reasons: item.reasons ?? [],
  }))
}
