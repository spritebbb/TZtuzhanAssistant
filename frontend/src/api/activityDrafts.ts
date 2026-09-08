import { apiFetch } from './index'

/** F06 可确认的活动草稿（后端签名短期引用，20 分钟过期）。 */
export interface ActivityDraft {
  draft_id: string
  kind: 'goal' | 'writing' | 'song_list' | 'book_list' | string
  title: string
  payload: Record<string, string>
  expires_at: string
}

export interface DraftConfirmResult {
  ok: boolean
  activity_id: number
  kind: string
  idempotent?: boolean
}

export async function confirmActivityDraft(
  draftId: string,
  patch: { title?: string; payload?: Record<string, string> } = {},
): Promise<DraftConfirmResult> {
  const response = await apiFetch('/api/activity-drafts/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      draft_id: draftId,
      title: patch.title ?? null,
      payload: patch.payload ?? null,
    }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '确认失败')
  return data as DraftConfirmResult
}
