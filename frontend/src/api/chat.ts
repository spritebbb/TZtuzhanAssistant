// 对话 API（SSE 流式）
import { apiFetch } from './index'

export interface ChatCallbacks {
  onPiece?: (piece: string) => void
  onDone?: (text: string) => void
  onError?: (err: string) => void
  onReset?: () => void
  onImageStart?: () => void
  onImageUrl?: (url: string) => void
  onConfirmRequest?: (req: any) => void
  onTool?: (event: ToolProgressEvent) => void
}

// 工具循环进度事件（后端 run_tool_loop 通过 on_progress 推送）
export interface ToolProgressEvent {
  type: 'thinking' | 'tool' | 'tool_done'
  name?: string
}

export async function streamChat(
  text: string,
  sessionId: string | null,
  signal: AbortSignal,
  cb: ChatCallbacks,
  image?: string | null,
): Promise<void> {
  const body = new URLSearchParams()
  body.set('text', text)
  if (sessionId) body.set('session_id', sessionId)
  if (image) body.set('image', image)

  const res = await apiFetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
    signal,
  })
  if (!res.ok || !res.body) throw new Error('HTTP ' + res.status)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      if (!chunk.startsWith('data: ')) continue
      try {
        const obj = JSON.parse(chunk.slice(6))
        if (obj.image_start) cb.onImageStart?.()
        else if (obj.image_url) cb.onImageUrl?.(obj.image_url)
        else if (obj.confirm_request) cb.onConfirmRequest?.(obj.confirm_request)
        else if (obj.tool) cb.onTool?.(obj.tool)
        else if (obj.piece !== undefined) cb.onPiece?.(obj.piece)
        else if (obj.done !== undefined) cb.onDone?.(obj.done)
        else if (obj.reset) cb.onReset?.()
        else if (obj.error) cb.onError?.(obj.error)
      } catch { /* ignore malformed chunk */ }
    }
  }
}

// 识图：返回描述 + 落盘后的图片 URL（图片已由后端存到 data/imgs/，前端可持久化展示）
export interface VisionResult {
  description: string
  imageUrl: string | null
}

export async function uploadVision(file: File): Promise<VisionResult> {
  const fd = new FormData()
  fd.append('file', file)
  const r = await apiFetch('/api/vision', { method: 'POST', body: fd })
  const d = await r.json()
  if (!r.ok || !d.description) throw new Error(d.error || '识图失败')
  return { description: d.description, imageUrl: d.image_url ?? null }
}
