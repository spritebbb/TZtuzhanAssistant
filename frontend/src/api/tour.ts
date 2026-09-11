// 能力演示脚本（后端 /api/tour）
import { apiFetch } from './index'

export interface TourStep {
  id: string
  /** 所属分组（记忆与了解 / 联网与信息 / …），与 groups 顺序一致 */
  group?: string
  /** chat=照原话发出去；ui=点界面按钮 */
  kind?: 'chat' | 'ui'
  title: string
  shows: string
  prompt: string
  follow_up?: string
  tools: string[]
  check: string
  /** 该步的前置条件（如需要 MCP 服务器已连接），没有则不显示 */
  needs?: string
}

export interface TourScript {
  title: string
  intro: string
  skill: string
  groups: string[]
  steps: TourStep[]
  /** 各带前置条件步骤的实时就绪探测（id → ready）；旧后端缺省为空 */
  ready?: Record<string, boolean>
}

export async function getTour(): Promise<TourScript> {
  const response = await apiFetch('/api/tour')
  if (!response.ok) throw new Error('演示脚本读取失败')
  const data = await response.json()
  const steps = (data.steps ?? []) as TourStep[]
  // 后端未给 groups 时（旧版本）按步骤顺序回退，保证面板仍能渲染
  const groups: string[] = Array.isArray(data.groups) && data.groups.length
    ? data.groups.map(String)
    : [...new Set(steps.map(s => s.group).filter((g): g is string => !!g))]
  return {
    title: String(data.title ?? '能力演示'),
    intro: String(data.intro ?? ''),
    skill: String(data.skill ?? ''),
    groups,
    steps,
    ready: (data.ready && typeof data.ready === 'object') ? data.ready as Record<string, boolean> : {},
  }
}
