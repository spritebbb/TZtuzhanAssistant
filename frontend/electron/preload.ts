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

// L09 本地语音输入：帧经主进程转发给本地 STT worker（无 shell、无任意模型路径）
contextBridge.exposeInMainWorld('tuzhanStt', {
  start: (opts: { language: string; modelRef: string }) =>
    ipcRenderer.invoke('stt:start', opts) as Promise<{ ok: boolean; error?: string }>,
  pushAudio: (chunk: Uint8Array) => {
    ipcRenderer.send('stt:audio', chunk)
  },
  stop: () => ipcRenderer.invoke('stt:stop') as Promise<{ ok: boolean }>,
  cancel: () => ipcRenderer.invoke('stt:cancel') as Promise<{ ok: boolean }>,
  onEvent: (cb: (ev: { op: string; request_id?: string; text?: string; code?: string; message?: string }) => void) => {
    const listener = (_e: Electron.IpcRendererEvent, ev: { op: string }) => cb(ev)
    ipcRenderer.on('stt:event', listener)
    return () => ipcRenderer.removeListener('stt:event', listener)
  },
})

// L10 桌面宠物：drag/close/toggleIgnoreMouse（宠物页）+ togglePet（设置页）
contextBridge.exposeInMainWorld('tuzhanPet', {
  drag: (delta: { dx: number; dy: number }) => {
    ipcRenderer.send('pet:drag', delta)
  },
  close: () => {
    ipcRenderer.send('pet:close')
  },
  toggleIgnoreMouse: (ignore: boolean) => {
    ipcRenderer.send('pet:toggleIgnoreMouse', ignore)
  },
  togglePet: () => ipcRenderer.invoke('pet:toggle') as Promise<boolean>,
})
