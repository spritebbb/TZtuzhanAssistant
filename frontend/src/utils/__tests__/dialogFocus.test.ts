import { afterEach, describe, expect, it } from 'vitest'

import { activeDialog, focusFirst, trapDialogTab } from '../dialogFocus'

afterEach(() => { document.body.innerHTML = '' })

describe('dialog keyboard focus', () => {
  it('ignores hidden overlays and focuses the first control in the active dialog', () => {
    document.body.innerHTML = `
      <div class="overlay"><section role="dialog"><button id="hidden">隐藏</button></section></div>
      <section role="dialog" aria-label="活动"><button id="close">关闭</button><input id="field"></section>
    `
    const dialog = activeDialog()
    expect(dialog?.getAttribute('aria-label')).toBe('活动')
    focusFirst(dialog)
    expect(document.activeElement?.id).toBe('close')
  })

  it('wraps Tab and Shift+Tab inside the dialog', () => {
    document.body.innerHTML = `
      <button id="outside">外部</button>
      <section role="dialog"><button id="first">一</button><button id="last">二</button></section>
    `
    const dialog = activeDialog()
    const first = document.querySelector<HTMLElement>('#first')!
    const last = document.querySelector<HTMLElement>('#last')!
    last.focus()
    const forward = new KeyboardEvent('keydown', { key: 'Tab', cancelable: true })
    expect(trapDialogTab(forward, dialog)).toBe(true)
    expect(document.activeElement).toBe(first)

    const backward = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, cancelable: true })
    expect(trapDialogTab(backward, dialog)).toBe(true)
    expect(document.activeElement).toBe(last)
  })

  it('prioritizes the latest urgent confirmation as the active dialog', () => {
    document.body.innerHTML = `
      <section role="dialog" aria-label="设置"><button>关闭</button></section>
      <section role="alertdialog" aria-label="确认写文件"><button id="reject">拒绝</button></section>
    `
    expect(activeDialog()?.getAttribute('aria-label')).toBe('确认写文件')
  })
})
