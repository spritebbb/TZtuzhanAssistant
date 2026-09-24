import { apiFetch, getApiUrl } from './index'

export type GoalStatus = 'active' | 'paused' | 'completed' | 'cancelled'
export type GoalSupportMode = 'companion' | 'reminder'

export interface GoalProgress {
  id: number
  content: string
  percent: number | null
  next_step: string
  ts: string
}

export interface SharedGoal {
  id: number
  kind: 'goal'
  title: string
  status: GoalStatus
  motivation: string
  next_step: string
  support_mode: GoalSupportMode
  reminder_at: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
  progress_entries: GoalProgress[]
  review: string
}

// P1-11：此前本模块整文件用裸 fetch（全仓唯一），丢失 apiFetch 的
// baseUrl 与 Authorization 注入——远程 token 模式全 401、Vite dev 模式
// 全 404、后端 423 锁定也不会触发全局锁屏。
async function goalRequest(path: string, init?: RequestInit): Promise<SharedGoal> {
  const response = await apiFetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  const data = await response.json()
  if (!response.ok || !data.ok || !data.goal) throw new Error(data.error || '共同目标更新失败')
  return data.goal as SharedGoal
}

export async function listGoals(): Promise<SharedGoal[]> {
  const response = await apiFetch('/api/goals')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '共同目标读取失败')
  return data.goals as SharedGoal[]
}

export function startGoal(payload: {
  title: string
  motivation: string
  next_step: string
  support_mode: GoalSupportMode
  reminder_at?: string | null
}): Promise<SharedGoal> {
  return goalRequest('/api/goals', { method: 'POST', body: JSON.stringify(payload) })
}

export function addGoalProgress(
  goalId: number,
  payload: { content: string; percent?: number | null; next_step?: string },
): Promise<SharedGoal> {
  return goalRequest(`/api/goals/${goalId}/progress`, { method: 'POST', body: JSON.stringify(payload) })
}

export function pauseGoal(goalId: number): Promise<SharedGoal> {
  return goalRequest(`/api/goals/${goalId}/pause`, { method: 'POST' })
}

export function resumeGoal(goalId: number): Promise<SharedGoal> {
  return goalRequest(`/api/goals/${goalId}/resume`, { method: 'POST' })
}

export function cancelGoal(goalId: number): Promise<SharedGoal> {
  return goalRequest(`/api/goals/${goalId}/cancel`, { method: 'POST' })
}

export function completeGoal(goalId: number, createArtifact: boolean): Promise<SharedGoal> {
  return goalRequest(`/api/goals/${goalId}/complete`, {
    method: 'POST',
    body: JSON.stringify({ create_artifact: createArtifact }),
  })
}

export function exportGoalUrl(goalId: number): string {
  // 绝对地址 + query token：`<a href>` 原生导航不带 Authorization 头（P2-20）
  return getApiUrl(`/api/goals/${goalId}/export?format=md`, true)
}
