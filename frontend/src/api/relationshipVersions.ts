import { apiFetch } from './index'

export interface RelationshipVersionSnapshotState {
  affection: number
  mood: number
  mood_label: string
  energy: number
  tension: number
  stage: string
  resting: boolean
}

export interface RelationshipVersionSnapshotSeason {
  code: string
  label: string
}

export interface RelationshipVersionSnapshotBehavior {
  mood_line: string
  stage_line: string
  texture_line: string
  initiative: string
  rest_line: string
  season_line: string
}

export interface RelationshipVersionSnapshotCounts {
  messages: number
  facts_active: number
  long_memory: number
  events_active: number
  artifacts_real: number
  artifacts_fiction: number
  activities_active: number
  activities_completed: number
  promises_pending: number
  promises_completed: number
  diary: number
  future_letters: number
  dual_perspectives: number
  relationship_snapshots: number
}

export interface RelationshipVersionSnapshot {
  format_version: number
  state: RelationshipVersionSnapshotState
  season: RelationshipVersionSnapshotSeason
  behavior: RelationshipVersionSnapshotBehavior
  counts: RelationshipVersionSnapshotCounts
}

export interface RelationshipVersion {
  id: number
  label: string
  captured_at: string
  schema_version: number
  created_at: string
  snapshot: RelationshipVersionSnapshot
}

export interface RelationshipVersionChange {
  key: string
  before: string | number | boolean | null
  after: string | number | boolean | null
}

export interface RelationshipVersionComparison {
  before: RelationshipVersion
  after: RelationshipVersion
  numeric_deltas: Record<string, number>
  changes: RelationshipVersionChange[]
}

function parseState(data: unknown): RelationshipVersionSnapshotState {
  const state = (data ?? {}) as Record<string, unknown>
  return {
    affection: Number(state.affection ?? 0),
    mood: Number(state.mood ?? 0),
    mood_label: String(state.mood_label ?? ''),
    energy: Number(state.energy ?? 0),
    tension: Number(state.tension ?? 0),
    stage: String(state.stage ?? ''),
    resting: Boolean(state.resting),
  }
}

function parseSeason(data: unknown): RelationshipVersionSnapshotSeason {
  const season = (data ?? {}) as Record<string, unknown>
  return { code: String(season.code ?? ''), label: String(season.label ?? '') }
}

function parseBehavior(data: unknown): RelationshipVersionSnapshotBehavior {
  const behavior = (data ?? {}) as Record<string, unknown>
  return {
    mood_line: String(behavior.mood_line ?? ''),
    stage_line: String(behavior.stage_line ?? ''),
    texture_line: String(behavior.texture_line ?? ''),
    initiative: String(behavior.initiative ?? ''),
    rest_line: String(behavior.rest_line ?? ''),
    season_line: String(behavior.season_line ?? ''),
  }
}

function parseCounts(data: unknown): RelationshipVersionSnapshotCounts {
  const counts = (data ?? {}) as Record<string, unknown>
  return {
    messages: Number(counts.messages ?? 0),
    facts_active: Number(counts.facts_active ?? 0),
    long_memory: Number(counts.long_memory ?? 0),
    events_active: Number(counts.events_active ?? 0),
    artifacts_real: Number(counts.artifacts_real ?? 0),
    artifacts_fiction: Number(counts.artifacts_fiction ?? 0),
    activities_active: Number(counts.activities_active ?? 0),
    activities_completed: Number(counts.activities_completed ?? 0),
    promises_pending: Number(counts.promises_pending ?? 0),
    promises_completed: Number(counts.promises_completed ?? 0),
    diary: Number(counts.diary ?? 0),
    future_letters: Number(counts.future_letters ?? 0),
    dual_perspectives: Number(counts.dual_perspectives ?? 0),
    relationship_snapshots: Number(counts.relationship_snapshots ?? 0),
  }
}

function parseSnapshot(data: unknown): RelationshipVersionSnapshot {
  const snapshot = (data ?? {}) as Record<string, unknown>
  return {
    format_version: Number(snapshot.format_version ?? 1),
    state: parseState(snapshot.state),
    season: parseSeason(snapshot.season),
    behavior: parseBehavior(snapshot.behavior),
    counts: parseCounts(snapshot.counts),
  }
}

function parseVersion(data: unknown): RelationshipVersion {
  const version = (data ?? {}) as Record<string, unknown>
  return {
    id: Number(version.id),
    label: String(version.label ?? ''),
    captured_at: String(version.captured_at ?? ''),
    schema_version: Number(version.schema_version ?? 0),
    created_at: String(version.created_at ?? ''),
    snapshot: parseSnapshot(version.snapshot),
  }
}

function parseComparison(data: unknown): RelationshipVersionComparison {
  const comparison = (data ?? {}) as Record<string, unknown>
  const deltas: Record<string, number> = {}
  const rawDeltas = (comparison.numeric_deltas ?? {}) as Record<string, unknown>
  for (const [key, value] of Object.entries(rawDeltas)) {
    deltas[key] = Number(value ?? 0)
  }
  const rawChanges = Array.isArray(comparison.changes) ? comparison.changes : []
  const changes: RelationshipVersionChange[] = rawChanges.map((item) => {
    const change = (item ?? {}) as Record<string, unknown>
    return {
      key: String(change.key ?? ''),
      before: (change.before ?? null) as RelationshipVersionChange['before'],
      after: (change.after ?? null) as RelationshipVersionChange['after'],
    }
  })
  return {
    before: parseVersion(comparison.before),
    after: parseVersion(comparison.after),
    numeric_deltas: deltas,
    changes,
  }
}

/** 列出关系版本检查点；flag 关闭或接口不可用时抛错，角落据此隐藏本区。 */
export async function listRelationshipVersions(): Promise<RelationshipVersion[]> {
  const response = await apiFetch('/api/relationship-versions')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '不同版本的我们未开启')
  return (Array.isArray(data.versions) ? data.versions : []).map(parseVersion)
}

export async function captureRelationshipVersion(label: string): Promise<RelationshipVersion> {
  const response = await apiFetch('/api/relationship-versions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok || !data.version) throw new Error(data.error || '版本没有留下，再试一次')
  return parseVersion(data.version)
}

export async function compareRelationshipVersions(
  beforeId: number,
  afterId: number,
): Promise<RelationshipVersionComparison> {
  const query = `before_id=${encodeURIComponent(String(beforeId))}&after_id=${encodeURIComponent(String(afterId))}`
  const response = await apiFetch(`/api/relationship-versions/compare?${query}`)
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '比较没有成功')
  return parseComparison(data)
}

export async function deleteRelationshipVersion(id: number): Promise<void> {
  const response = await apiFetch(`/api/relationship-versions/${id}`, { method: 'DELETE' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '删除没有成功')
}
