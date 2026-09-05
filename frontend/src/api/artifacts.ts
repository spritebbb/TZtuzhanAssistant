import { apiFetch } from './index'

export interface ArtifactItem {
  id: number
  artifact_type: string
  source_type: string
  source_id: number
  title: string
  content: string
  version: number
  created_at: string
  updated_at: string
}

export async function listArtifacts(limit = 50): Promise<ArtifactItem[]> {
  const response = await apiFetch(`/api/artifacts?limit=${limit}`)
  if (!response.ok) throw new Error('我们的角落读取失败')
  const data = await response.json()
  return Array.isArray(data.artifacts) ? data.artifacts : []
}
