// Markdown 渲染（使用 marked + DOMPurify 防 XSS）

import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({
  breaks: true,
  gfm: true,
})

export function renderMarkdown(text: string): string {
  if (!text) return ''
  const raw = marked.parse(text, { async: false }) as string
  const clean = DOMPurify.sanitize(raw, {
    USE_PROFILES: { html: true },
    ADD_TAGS: ['img'],
  })
  return hardenLinks(clean)
}

// 聊天里的链接一律新窗口打开：Electron 主进程把「新窗口」请求交给系统浏览器，
// 否则点击会在应用窗口内直接导航——聊天界面被顶掉，窗口又没有地址栏和后退键，
// 用户回不来（2026-09-09 用户实测：点开演示里查文档给的网址后卡在外部页面）。
// 属性在 sanitize 之后补：这里是本文件写死的固定值，不引入用户可控内容。
//
// 同时显式做协议白名单：DOMPurify 在链接被 <p> 包裹时会原样放行
// `javascript:`（裸 <a> 会剥掉、包一层就漏，2026-09-09 实测），
// marked 输出的链接恰好总在 <p> 里，所以不能只依赖上游配置。
const SAFE_LINK_SCHEME = new Set(['http', 'https', 'mailto', 'tel'])

function isSafeHref(href: string): boolean {
  // 浏览器解析协议时会忽略控制字符与空白（`java\tscript:` 即 javascript:），
  // 校验前按同样规则归一化，避免这类绕过。
  const normalized = href.replace(/[\u0000-\u0020]+/g, '')
  if (!normalized) return false
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(normalized)
  if (!scheme) return true  // 相对路径 / 锚点 / 协议相对，交给同源处理
  return SAFE_LINK_SCHEME.has(scheme[1].toLowerCase())
}

function hardenLinks(html: string): string {
  if (!html.includes('<a ')) return html
  const doc = new DOMParser().parseFromString(html, 'text/html')
  for (const anchor of Array.from(doc.querySelectorAll('a[href]'))) {
    if (!isSafeHref(anchor.getAttribute('href') || '')) {
      anchor.removeAttribute('href')  // 危险协议：退化成纯文字，不可点击
      continue
    }
    anchor.setAttribute('target', '_blank')
    anchor.setAttribute('rel', 'noopener noreferrer')
  }
  return doc.body.innerHTML
}