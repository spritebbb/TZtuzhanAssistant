// 能力演示脚本（后端 /api/tour）
import { apiFetch } from './index'

export interface TourStep {
  id: string
  title: string
  shows: string
  prompt: string
  follow_up?: string
  tools: string[]
  check: string
}

export interface TourScript {
  title: string
  intro: string
  skill: string
  steps: TourStep[]
}

export async function getTour(): Promise<TourScript> {
  const response = await apiFetch('/api/tour')
  if (!response.ok) throw new Error('演示脚本读取失败')
  const data = await response.json()
  return {
    title: String(data.title ?? '能力演示'),
    intro: String(data.intro ?? ''),
    skill: String(data.skill ?? ''),
    steps: (data.steps ?? []) as TourStep[],
  }
}
