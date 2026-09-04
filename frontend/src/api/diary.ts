import { apiFetch } from './index'

export interface DiaryEntry {
  id: number
  date: string
  content: string
  mood: string
  ts: string
}

export interface ResearchReport {
  id: number
  period: string
  title: string
  content: string
  ts: string
}

export async function getDiaries(): Promise<DiaryEntry[]> {
  const response = await apiFetch('/api/diary?limit=120')
  if (!response.ok) throw new Error('日记读取失败')
  const data = await response.json()
  return Array.isArray(data.diaries) ? data.diaries : []
}

export async function getResearchReports(): Promise<ResearchReport[]> {
  const response = await apiFetch('/api/research-reports?limit=40')
  if (!response.ok) throw new Error('研究记录读取失败')
  const data = await response.json()
  return Array.isArray(data.reports) ? data.reports : []
}
