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

  // 回归：聊天里的链接必须新窗口打开。Electron 主进程把「新窗口」请求交给系统
  // 浏览器；若链接在同窗口导航，聊天界面会被外部页面顶掉且没有返回入口
  //（2026-09-09 用户实测：点开演示里查文档给的网址后卡在外部页面）。
  it('markdown 链接新窗口打开并带 noopener', () => {
    const html = renderMarkdown('看下 [FastAPI 文档](https://fastapi.tiangolo.com/)')
    expect(html).toContain('href="https://fastapi.tiangolo.com/"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('裸链接同样被加固', () => {
    const html = renderMarkdown('参考 https://example.com/doc 这一篇')
    expect(html).toContain('href="https://example.com/doc"')
    expect(html).toContain('target="_blank"')
  })

  it('危险协议仍被清理', () => {
    const html = renderMarkdown('[点我](javascript:alert(1))')
    expect(html).not.toContain('javascript:')
  })
})
