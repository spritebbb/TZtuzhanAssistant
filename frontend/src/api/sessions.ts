// 会话 API（单一会话模式 + 归档）
import { apiFetch, getBaseUrl } from './index'

export interface SessionInfo {
  id: string
  title: string
  created_at: number
  updated_at: number
  count: number
}

export interface Message {
  role: 'user' | 'bot'
  content: string
  image?: string | null
  ts: number
}

export interface ArchiveInfo {
  id: string
  title: string
  created_at: number
  message_count: number
}

export interface ArchiveDetail extends ArchiveInfo {
  messages: Message[]
}

/** 归档搜索结果摘要（列表展示用，不含完整消息）。 */
export interface ArchiveSearchResult {
  id: string
  title: string
  created_at: number
  message_count: number
  preview: string
}

/** 彻底重置（失忆重开）的返回统计。 */
export interface ResetStats {
  userdb_tables: number
  vector: number
  session_msgs: number
}

// 单一会话固定 id（与后端 CURRENT_SESSION_ID 对齐）
export const CURRENT_SESSION_ID = 'current'

export async function getMessages(id: string): Promise<Message[]> {
  const r = await apiFetch(`/api/sessions/${id}`)
  if (!r.ok) return []
  return r.json()
}

// ---- 归档 ----

/** 结束并归档当前会话：后端打包当前消息存入归档库，清空当前会话。 */
export async function archiveCurrent(): Promise<ArchiveInfo | null> {
  const r = await apiFetch('/api/sessions/archive', { method: 'POST' })
  const d = await r.json()
  if (!r.ok || !d.archived) return null
  return d.archive ?? null
}

/** 彻底重置（失忆重开）：清空菟菚积累的记忆/好感/昵称/向量 + 当前会话气泡。 */
export async function resetUser(): Promise<ResetStats> {
  const r = await apiFetch('/api/user/reset', { method: 'POST' })
  const d = await r.json()
  if (!r.ok || !d.ok) throw new Error(d.error || '重置失败')
  return d.reset ?? { userdb_tables: 0, vector: 0, session_msgs: 0 }
}

/** 归档列表（只读回看）。 */
export async function listArchives(): Promise<ArchiveInfo[]> {
  const r = await apiFetch('/api/sessions/archives')
  if (!r.ok) return []
  const d = await r.json()
  return d.archives ?? []
}

/** 单条归档详情（含完整消息）。 */
export async function getArchive(id: string): Promise<ArchiveDetail | null> {
  const r = await apiFetch(`/api/sessions/archives/${id}`)
  if (!r.ok) return null
  const d = await r.json()
  return d.archive ?? null
}

/** 按关键词搜索归档（标题 + 内容），后端返回摘要列表（标题/条数/命中预览）。 */
export async function searchArchives(q: string): Promise<ArchiveSearchResult[]> {
  const r = await apiFetch(`/api/sessions/archives/search?q=${encodeURIComponent(q)}`)
  if (!r.ok) return []
  const d = await r.json()
  return d.results ?? []
}

// 菟菚主动消息：后端已生成、待投递队列里的一条（取走后即清空）
export async function getInitiative(sessionId: string): Promise<string | null> {
  const r = await apiFetch(`/api/initiative?session_id=${encodeURIComponent(sessionId)}`)
  const d = await r.json()
  return d.initiative ?? null
}

// 菟菚主动消息 SSE 长连接：服务端后台生成主动消息时秒级推送（替代 30s 轮询）
export function openInitiativeStream(
  sessionId: string,
  onMessage: (text: string) => void,
  onError?: () => void,
): EventSource {
  const url = `${getBaseUrl()}/api/initiative/stream?session_id=${encodeURIComponent(sessionId)}`
  const es = new EventSource(url)
  es.addEventListener('initiative', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      if (data && data.text) onMessage(data.text)
    } catch {
      // 解析失败忽略
    }
  })
  if (onError) es.onerror = onError
  return es
}