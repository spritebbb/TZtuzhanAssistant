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

export interface KnowledgeOpinion {
  id: number
  document_id: number
  filename: string
  stance: string
  origin: string
  confidence: number
  version: number
  created_at: string
  source_spans: Array<{ chunk_id: number; start_offset: number; end_offset: number }>
}

export async function listKnowledgeOpinions(): Promise<KnowledgeOpinion[]> {
  const response = await apiFetch('/api/knowledge/opinions')
  if (!response.ok) throw new Error('观点读取失败')
  const data = await response.json()
  return (data.opinions ?? []) as KnowledgeOpinion[]
}

export async function extractKnowledgeOpinions(docId: number): Promise<{ ok: boolean; opinions?: KnowledgeOpinion[]; note?: string; error?: string }> {
  const response = await apiFetch(`/api/knowledge/documents/${docId}/extract-opinions`, { method: 'POST' })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    return { ok: false, error: data.error || '她还没读出什么观点，过会儿再试' }
  }
  return (await response.json()) as { ok: boolean; opinions?: KnowledgeOpinion[]; note?: string }
}

export async function revokeKnowledgeOpinion(id: number): Promise<boolean> {
  const response = await apiFetch(`/api/knowledge/opinions/${id}`, { method: 'DELETE' })
  if (!response.ok) return false
  const data = await response.json()
  return Boolean(data.ok)
}

/** L01 网页导入：返回 job_id（worker 抓取解析，可查询与取消）。 */
export async function importKnowledgeUrl(url: string): Promise<{ job_id: string; status: string }> {
  const response = await apiFetch('/api/knowledge/import-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '网页导入失败')
  return { job_id: data.job_id, status: data.status }
}

export async function getImportJob(jobId: string): Promise<{ status: string; error?: string; document_id?: number }> {
  const response = await apiFetch(`/api/knowledge/import-jobs/${jobId}`)
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '任务查询失败')
  return data
}

export async function cancelImportJob(jobId: string): Promise<void> {
  const response = await apiFetch(`/api/knowledge/import-jobs/${jobId}/cancel`, { method: 'POST' })
  if (!response.ok) throw new Error('取消失败')
}
