// NP-09 错误提示分区对待：聊天流外统一轻量 toast。
// 用法：notify('保存失败：xxx')。App.vue 挂载 toast 容器监听 tztuzhan:notify 事件。
// 聊天流内（ChatView/MessageBubble 域）保持人设化文案，不走此通道。
export const NOTIFY_EVENT = 'tztuzhan:notify'

export interface NotifyPayload {
  message: string
  kind: 'error' | 'info'
}

export function notify(message: string, kind: 'error' | 'info' = 'error'): void {
  window.dispatchEvent(new CustomEvent<NotifyPayload>(NOTIFY_EVENT, { detail: { message, kind } }))
}
