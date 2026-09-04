import { apiFetch } from './index'

export interface UsageBucket {
  prompt: number
  completion: number
  calls: number
  estimated: number
  cost: number
}

export interface UsageChannel {
  channel: string
  prompt: number
  completion: number
  calls: number
  cost: number
}

export interface UsageSummary {
  today: UsageBucket
  period: UsageBucket
  days: number
  by_channel: UsageChannel[]
  prices: { input_per_mtok: number; output_per_mtok: number }
}

export async function getUsageSummary(days = 7): Promise<UsageSummary> {
  const response = await apiFetch(`/api/usage/summary?days=${days}`)
  if (!response.ok) throw new Error('用量读取失败')
  const data = await response.json()
  return data.usage as UsageSummary
}
