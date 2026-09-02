// 图片 URL 工具
import { getBaseUrl } from '../api'

// 后端返回的图片 URL 是相对路径（如 /api/images/x.png），
// Electron 生产环境从 file:// 加载页面时相对路径会失效，这里统一拼成绝对地址
export function resolveImageSrc(src: string | null | undefined): string {
  if (!src) return ''
  if (/^https?:\/\//i.test(src)) return src
  return getBaseUrl() + (src.startsWith('/') ? src : '/' + src)
}
