// 菟菚助手 · PWA Service Worker
// 缓存策略：Network First，但只缓存「静态导航 + 静态资源」。
// /api/*、/mcp/*、text/event-stream 一律放行（不缓存、不 clone）：
//   - /api/chat（POST SSE）、/api/initiative/stream（EventSource）是长连接流，
//     cache.put 会把整个流 body 读进 Cache Storage，且长连接期间 clone 一直挂起；
//   - /api/audit、/api/config、/api/sessions 等响应含工具参数/日志等敏感内容，
//     不应落盘；
//   - /api/images、/persona 图片量大且无清理策略，只增不减。

// 资源更新（新立绘与新版前端构建）时递增版本，激活后清理旧构建缓存。
const CACHE = 'tztuzhan-v2'
const PRECACHE = ['/', '/manifest.json', '/icon.svg', '/favicon.ico']

// 需要走 network-first 缓存策略的静态资源（其余一律直连网络不缓存）
const CACHEABLE = /^(\/assets\/|\/favicon\.ico$|\/manifest\.json$|\/icon\.svg$)|\/$/
const SKIP = /^\/(api|mcp)\//

self.addEventListener('install', (e) => {
  self.skipWaiting()
  e.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)),
  )
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  )
  self.clients.claim()
})

self.addEventListener('fetch', (e) => {
  const req = e.request
  // 只处理同源 GET 请求
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return

  const path = url.pathname
  // API / MCP / 流式响应直接放行，绝不进缓存
  if (SKIP.test(path)) return

  // 目标 response 的 content-type 为 event-stream / 流时也不缓存（双保险）
  const isStream = (res) => {
    const ct = res.headers.get('content-type') || ''
    return ct.includes('text/event-stream') || ct.includes('application/octet-stream')
  }

  // 只对可缓存的静态资源走 network-first；其余（如 /persona/*）直连不缓存
  if (!CACHEABLE.test(path)) return

  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok && !isStream(res)) {
          const clone = res.clone()
          caches.open(CACHE).then((cache) => cache.put(req, clone))
        }
        return res
      })
      .catch(() => caches.match(req)),
  )
})
