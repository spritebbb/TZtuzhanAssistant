import { apiFetch } from './index'

export interface KnowledgeDocument {
  id: number
  filename: string
  format: string
  size_bytes: number
  chunk_count: number
  ts: string
}

export interface UploadResult {
  ok: boolean
  document?: KnowledgeDocument & { indexed?: number }
  error?: string
}

export async function listKnowledgeDocuments(): Promise<KnowledgeDocument[]> {
  const response = await apiFetch('/api/knowledge/documents')
  if (!response.ok) throw new Error('知识库读取失败')
  const data = await response.json()
  return data.documents as KnowledgeDocument[]
}

export async function uploadKnowledgeDocument(file: File): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  const response = await apiFetch('/api/knowledge/upload', { method: 'POST', body: form })
  if (!response.ok) return { ok: false, error: '上传失败，过会儿再试' }
  return (await response.json()) as UploadResult
}

export async function deleteKnowledgeDocument(id: number): Promise<boolean> {
  const response = await apiFetch(`/api/knowledge/documents/${id}`, { method: 'DELETE' })
  if (!response.ok) return false
  const data = await response.json()
  return Boolean(data.ok)
}
