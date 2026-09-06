import { apiFetch } from './index'

export interface SnapshotMilestone {
  days: number
  eligible: boolean
  has_snapshot: boolean
  snapshot_id: number | null
}

export interface SnapshotSourceCount {
  count: number
  omitted: number
  cap: number
}

export interface RelationshipSnapshot {
  id: number
  snapshot_days: number
  start_date: string
  cutoff_date: string
  generated_at: string
  created_at: string
  updated_at: string
  /** 每类来源的收录/截断计数；截断必须在界面上显式可见 */
  source_counts: Record<string, SnapshotSourceCount>
  rendered_markdown: string
}

export interface RelationshipSnapshotsBoard {
  start_date: string | null
  today: string
  days_since: number | null
  milestones: SnapshotMilestone[]
  snapshots: RelationshipSnapshot[]
}

export async function listRelationshipSnapshots(): Promise<RelationshipSnapshotsBoard> {
  const response = await apiFetch('/api/relationship-snapshots')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '纪念页读取失败')
  return {
    start_date: data.start_date ?? null,
    today: typeof data.today === 'string' ? data.today : '',
    days_since: data.days_since ?? null,
    milestones: Array.isArray(data.milestones) ? data.milestones : [],
    snapshots: Array.isArray(data.snapshots) ? data.snapshots : [],
  }
}

export async function createRelationshipSnapshot(days: number): Promise<RelationshipSnapshot> {
  const response = await apiFetch('/api/relationship-snapshots', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ snapshot_days: days }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok || !data.snapshot) {
    throw new Error(data.error || '这一页没有整理好，再试一次')
  }
  return data.snapshot as RelationshipSnapshot
}

export async function deleteRelationshipSnapshot(days: number): Promise<void> {
  const response = await apiFetch(`/api/relationship-snapshots/${days}`, { method: 'DELETE' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '删除没有成功')
}
