import { apiFetch } from './index'

export type UnlockType = 'date' | 'goal' | 'event'
export type LetterStatus = 'sealed' | 'ready' | 'opened'

export interface FutureLetter {
  id: number
  title: string
  unlock_type: UnlockType
  unlock_at: string | null
  goal_id: number | null
  goal_title: string
  event_type: string | null
  status: LetterStatus
  unlocked_at: string | null
  unlocked_by_event_id: number | null
  created_at: string
  opened_at: string | null
  /** 只有已拆开（opened）的信才有正文；锁定态后端根本不返回这个字段 */
  body?: string
}

export interface GoalOption {
  id: number
  title: string
  status: 'active' | 'paused'
}

export interface EventTypeOption {
  type: string
  label: string
}

export interface FutureLettersBoard {
  letters: FutureLetter[]
  goal_options: GoalOption[]
  event_types: EventTypeOption[]
}

async function letterRequest(path: string, init?: RequestInit): Promise<FutureLetter> {
  const response = await apiFetch(path, init)
  const data = await response.json()
  if (!response.ok || !data.ok || !data.letter) {
    throw new Error(data.error || '信没有寄出去，再试一次')
  }
  return data.letter as FutureLetter
}

export async function listFutureLetters(): Promise<FutureLettersBoard> {
  const response = await apiFetch('/api/future-letters')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '未来信件读取失败')
  return {
    letters: Array.isArray(data.letters) ? data.letters : [],
    goal_options: Array.isArray(data.goal_options) ? data.goal_options : [],
    event_types: Array.isArray(data.event_types) ? data.event_types : [],
  }
}

export function createFutureLetter(payload: {
  body: string
  unlock_type: UnlockType
  title?: string
  unlock_at?: string
  goal_id?: number
  event_type?: string
}): Promise<FutureLetter> {
  return letterRequest('/api/future-letters', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function openFutureLetter(letterId: number): Promise<FutureLetter> {
  return letterRequest(`/api/future-letters/${letterId}/open`, { method: 'POST' })
}

export async function deleteFutureLetter(letterId: number): Promise<void> {
  const response = await apiFetch(`/api/future-letters/${letterId}`, { method: 'DELETE' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '信件删除失败')
}
