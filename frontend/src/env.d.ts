/// <reference types="vite/client" />

interface Window {
  electronAPI?: {
    getBackendUrl: () => Promise<string>
    getVersion: () => Promise<string>
    notify: (title: string, body: string) => Promise<boolean>
    focusWindow: () => Promise<boolean>
    setActiveSession: (sessionId: string | null) => Promise<boolean>
    onInitiativeMessage: (cb: (message: { text: string; image?: string | null }) => void) => (() => void)
    /** 38项#25 截图求助热键（仅桌面壳注入；PWA 无） */
    onHotkeyScreenshot: (cb: (shot: { buffer: ArrayBuffer; name: string }) => void) => (() => void)
    /** NP-10 桌面四件套（仅桌面壳注入） */
    setAlwaysOnTop: (on: boolean) => Promise<boolean>
    getAlwaysOnTop: () => Promise<boolean>
    setLaunchAtLogin: (on: boolean) => Promise<boolean>
    getLaunchAtLogin: () => Promise<boolean>
    setMainHotkey: (hotkey: string) => Promise<boolean>
    getMainHotkey: () => Promise<string>
    getHotkeyChoices: () => Promise<string[]>
  }
  /** L09 本地语音输入桥（仅桌面版注入；PWA 里为 undefined，按钮应禁用） */
  tuzhanStt?: {
    start: (opts: { language: string; modelRef: string }) => Promise<{ ok: boolean; error?: string }>
    pushAudio: (chunk: Uint8Array) => void
    stop: () => Promise<{ ok: boolean }>
    cancel: () => Promise<{ ok: boolean }>
    onEvent: (cb: (ev: { op: string; request_id?: string; text?: string; code?: string; message?: string }) => void) => (() => void)
  }
  /** L10 桌面宠物桥（宠物窗口与主窗口设置页共用；PWA 里为 undefined） */
  tuzhanPet?: {
    drag: (delta: { dx: number; dy: number }) => void
    close: () => void
    toggleIgnoreMouse: (ignore: boolean) => void
    togglePet: () => Promise<boolean>
    getPetState: () => Promise<boolean>
  }
  /** NP-11 迷你速聊窗桥（仅迷你窗内使用） */
  tuzhanMini?: {
    hide: () => void
  }
}
