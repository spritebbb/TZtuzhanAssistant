/// <reference types="vite/client" />

interface Window {
  electronAPI?: {
    getBackendUrl: () => Promise<string>
    getVersion: () => Promise<string>
    notify: (title: string, body: string) => Promise<boolean>
    focusWindow: () => Promise<boolean>
    setActiveSession: (sessionId: string | null) => Promise<boolean>
    onInitiativeMessage: (cb: (text: string) => void) => (() => void)
  }
}
