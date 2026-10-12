import { apiFetch } from './index'

export interface DiaryEntry {
  id: number
  date: string
  content: string
  mood: string
  ts: string
}

export async function getDiaries(): Promise<DiaryEntry[]> {
  const response = await apiFetch('/api/diary?limit=120')
  if (!response.ok) throw new Error('日记读取失败')
  const data = await response.json()
  return Array.isArray(data.diaries) ? data.diaries : []
}
