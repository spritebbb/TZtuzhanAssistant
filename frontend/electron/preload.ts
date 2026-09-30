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
  // NP-10 桌面四件套：置顶 / 自启 / 全局热键（网页版无此桥，设置区隐藏）
  setAlwaysOnTop: (on: boolean) => ipcRenderer.invoke('ui:set-always-on-top', on) as Promise<boolean>,
  getAlwaysOnTop: () => ipcRenderer.invoke('ui:get-always-on-top') as Promise<boolean>,
  setLaunchAtLogin: (on: boolean) => ipcRenderer.invoke('ui:set-launch-at-login', on) as Promise<boolean>,
  getLaunchAtLogin: () => ipcRenderer.invoke('ui:get-launch-at-login') as Promise<boolean>,
  setMainHotkey: (hotkey: string) => ipcRenderer.invoke('ui:set-main-hotkey', hotkey) as Promise<boolean>,
  getMainHotkey: () => ipcRenderer.invoke('ui:get-main-hotkey') as Promise<string>,
  getHotkeyChoices: () => ipcRenderer.invoke('ui:get-hotkey-choices') as Promise<string[]>,
  // 订阅主进程转发的主动消息（主进程轮询到后推送过来，用于追加气泡）
  onInitiativeMessage: (cb: (message: { text: string; image?: string | null }) => void) => {
    const listener = (_e: Electron.IpcRendererEvent, message: { text: string; image?: string | null }) => cb(message)
    ipcRenderer.on('initiative-message', listener)
    return () => ipcRenderer.removeListener('initiative-message', listener)
  },
  // 38项#25 截图求助热键：主进程抓屏的 PNG 字节推过来（渲染侧转 File 走识图链路）
  onHotkeyScreenshot: (cb: (shot: { buffer: ArrayBuffer; name: string }) => void) => {
    const listener = (_e: Electron.IpcRendererEvent, shot: { buffer: ArrayBuffer; name: string }) => cb(shot)
    ipcRenderer.on('hotkey-screenshot', listener)
    return () => ipcRenderer.removeListener('hotkey-screenshot', listener)
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
  // NP-06：宠物开关真实状态查询（设置页/首次向导回显；此前恒 false 会误关）
  getPetState: () => ipcRenderer.invoke('pet:get-state') as Promise<boolean>,
})

// NP-11 迷你速聊窗：答完 3 秒自动隐藏经此桥请求主进程收窗
contextBridge.exposeInMainWorld('tuzhanMini', {
  hide: () => {
    ipcRenderer.send('mini:hide')
  },
})
