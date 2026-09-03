// 后端 API 客户端
// 在 Electron 中通过 preload 获取后端地址；开发模式下默认 127.0.0.1:8801

let baseUrl = window.location.protocol === 'http:' || window.location.protocol === 'https:'
  ? window.location.origin
  : 'http://127.0.0.1:8801'

const TOKEN_KEY = 'tztuzhan-remote-token'
let remoteToken = ''

function captureRemoteToken(): void {
  try {
    const url = new URL(window.location.href)
    const supplied = url.searchParams.get('token')?.trim()
    if (supplied) {
      remoteToken = supplied
      window.sessionStorage.setItem(TOKEN_KEY, supplied)
      url.searchParams.delete('token')
      window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
    } else {
      remoteToken = window.sessionStorage.getItem(TOKEN_KEY) || ''
    }
  } catch {
    remoteToken = ''
  }
}

captureRemoteToken()

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

export function getApiUrl(path: string, queryToken = false): string {
  const url = new URL(path, `${baseUrl}/`)
  if (queryToken && remoteToken) url.searchParams.set('token', remoteToken)
  return url.toString()
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  await resolveBaseUrl()
  const headers = new Headers(init?.headers)
  if (remoteToken && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${remoteToken}`)
  }
  return fetch(getApiUrl(path), { ...init, headers })
}
