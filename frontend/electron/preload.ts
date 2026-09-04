import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),
  getVersion: () => ipcRenderer.invoke('get-version'),
  // 桌面系统通知：菟菚主动消息弹出（点击可聚焦窗口）
  notify: (title: string, body: string) => ipcRenderer.invoke('notify', { title, body }),
  // 聚焦主窗口（点击通知后调用）
  focusWindow: () => ipcRenderer.invoke('focus-window'),
  // 上报「当前会话 id」给主进程，让它独立轮询主动消息（关窗也能弹通知）
  setActiveSession: (sessionId: string | null) => ipcRenderer.invoke('set-active-session', sessionId),
  // 订阅主进程转发的主动消息（主进程轮询到后推送过来，用于追加气泡）
  onInitiativeMessage: (cb: (message: { text: string; image?: string | null }) => void) => {
    const listener = (_e: Electron.IpcRendererEvent, message: { text: string; image?: string | null }) => cb(message)
    ipcRenderer.on('initiative-message', listener)
    return () => ipcRenderer.removeListener('initiative-message', listener)
  },
})
