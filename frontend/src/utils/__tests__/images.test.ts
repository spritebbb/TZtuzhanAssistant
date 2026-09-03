import { describe, expect, it, vi } from 'vitest'
import { resolveImageSrc } from '../images'

// api/index.ts 的 baseUrl 默认 127.0.0.1:8801；这里直接验证拼接逻辑
vi.mock('../../api', () => ({
  getApiUrl: (path: string) => `http://127.0.0.1:8801${path}`,
}))

describe('resolveImageSrc', () => {
  it('空值返回空串', () => {
    expect(resolveImageSrc(null)).toBe('')
    expect(resolveImageSrc(undefined)).toBe('')
    expect(resolveImageSrc('')).toBe('')
  })

  it('相对路径拼成后端绝对地址', () => {
    expect(resolveImageSrc('/api/images/a.png')).toBe('http://127.0.0.1:8801/api/images/a.png')
  })

  it('缺少前导斜杠时补上', () => {
    expect(resolveImageSrc('api/images/a.png')).toBe('http://127.0.0.1:8801/api/images/a.png')
  })

  it('已是完整 URL 则原样返回', () => {
    expect(resolveImageSrc('https://example.com/x.png')).toBe('https://example.com/x.png')
    expect(resolveImageSrc('http://127.0.0.1:8801/api/images/a.png')).toBe('http://127.0.0.1:8801/api/images/a.png')
  })
})
