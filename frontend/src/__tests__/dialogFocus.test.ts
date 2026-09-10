import { afterEach, expect, it } from 'vitest'
import { focusableElements, trapDialogTab } from '../utils/dialogFocus'

afterEach(() => {
  document.body.innerHTML = ''
})

function mountDialog(): HTMLElement {
  const dialog = document.createElement('section')
  dialog.setAttribute('role', 'dialog')
  dialog.innerHTML = `
    <button id="first">首项</button>
    <input id="css-hidden" type="file" style="display: none">
    <div style="display: none"><button id="hidden-by-parent">隐藏祖先内</button></div>
    <button id="last">末项</button>
  `
  document.body.appendChild(dialog)
  return dialog
}

it('excludes controls hidden by CSS or an ancestor', () => {
  const dialog = mountDialog()
  expect(focusableElements(dialog).map(element => element.id)).toEqual(['first', 'last'])
})

it('wraps reverse Tab onto the last visible control', () => {
  const dialog = mountDialog()
  const first = dialog.querySelector<HTMLButtonElement>('#first')!
  const last = dialog.querySelector<HTMLButtonElement>('#last')!
  first.focus()

  const event = new KeyboardEvent('keydown', {
    key: 'Tab',
    shiftKey: true,
    bubbles: true,
    cancelable: true,
  })

  expect(trapDialogTab(event, dialog)).toBe(true)
  expect(document.activeElement).toBe(last)
})
