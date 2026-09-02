import { describe, expect, it } from 'vitest'
import { renderMarkdown } from '../markdown'

describe('renderMarkdown', () => {
  it('空文本返回空串', () => {
    expect(renderMarkdown('')).toBe('')
  })

  it('渲染基本 Markdown', () => {
    const html = renderMarkdown('**加粗** 和 `code`')
    expect(html).toContain('<strong>加粗</strong>')
    expect(html).toContain('<code>code</code>')
  })

  it('XSS 脚本被 DOMPurify 剥离', () => {
    const html = renderMarkdown('<script>alert(1)</script>hello')
    expect(html).not.toContain('<script')
    expect(html).toContain('hello')
  })

  it('onerror 事件属性被剥离', () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)>')
    expect(html).not.toContain('onerror')
  })
})
