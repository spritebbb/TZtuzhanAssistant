import { apiFetch } from './index'

export interface DashboardPoint {
  date: string
  affection: number
  mood: number
  messages: number
  user_messages: number
  tokens: number
  calls: number
}

export interface DashboardSummary {
  days: number
  current: {
    affection: {
      value: number
      stage: string
      bond: string
      next: string
      next_at: number | null
      fill: number
    }
    mood: { value: number; label: string }
    energy: number
    resting: boolean
    pending_promises: number
  }
  timeline: DashboardPoint[]
  stats: {
    active_days: number
    messages: number
    user_messages: number
    tokens: number
    cost: number
    diaries: number
    unlocks: number
    unlock_total: number
  }
  promises: Array<{
    id: number
    content: string
    follow_up: string
    created_at: string
  }>
  recent_affection: Array<{
    value: number
    delta: number
    reason: string
    ts: string
  }>
}

export async function getDashboard(days = 30): Promise<DashboardSummary> {
  const response = await apiFetch(`/api/dashboard?days=${days}`)
  if (!response.ok) throw new Error('成长总览读取失败')
  const data = await response.json()
  if (!data.ok || !data.dashboard) throw new Error(data.error || '成长总览读取失败')
  return data.dashboard as DashboardSummary
}
