// 后端 API 客户端
// 在 Electron 中通过 preload 获取后端地址；开发模式下默认 127.0.0.1:8801

let baseUrl = 'http://127.0.0.1:8801'

async function resolveBaseUrl(): Promise<string> {
  if (window.electronAPI) {
    try {
      const url = await window.electronAPI.getBackendUrl()
      if (url) baseUrl = url
    } catch {
      // fallback to default
    }
  }
  return baseUrl
}

export function getBaseUrl(): string {
  return baseUrl
}

export async function ensureBaseUrl(): Promise<string> {
  return resolveBaseUrl()
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  await resolveBaseUrl()
  return fetch(`${baseUrl}${path}`, init)
}