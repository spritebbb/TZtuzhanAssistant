const FOCUSABLE = [
  'button:not([disabled])',
  'a[href]',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'summary',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function isAvailable(element: HTMLElement): boolean {
  if (element.matches(':disabled') || element.closest('[hidden], [inert], [aria-hidden="true"]')) {
    return false
  }
  const overlay = element.closest<HTMLElement>('.overlay')
  if (overlay && !overlay.classList.contains('show')) return false
  if (typeof window === 'undefined') return true

  let current: HTMLElement | null = element
  while (current) {
    const style = window.getComputedStyle(current)
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') {
      return false
    }
    current = current.parentElement
  }
  return true
}

export function activeDialog(root: ParentNode = document): HTMLElement | null {
  const dialogs = Array.from(root.querySelectorAll<HTMLElement>('[role="dialog"], [role="alertdialog"]'))
    .filter(isAvailable)
  return dialogs.at(-1) || null
}

export function focusableElements(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(isAvailable)
}

export function focusFirst(dialog: HTMLElement | null): void {
  if (!dialog) return
  const preferred = dialog.querySelector<HTMLElement>('[autofocus], [data-autofocus]')
  const target = preferred && isAvailable(preferred)
    ? preferred
    : focusableElements(dialog)[0] || dialog
  if (target === dialog && !dialog.hasAttribute('tabindex')) dialog.tabIndex = -1
  target.focus()
}

export function trapDialogTab(event: KeyboardEvent, dialog: HTMLElement | null): boolean {
  if (event.key !== 'Tab' || !dialog) return false
  const items = focusableElements(dialog)
  if (!items.length) {
    event.preventDefault()
    focusFirst(dialog)
    return true
  }
  const first = items[0]
  const last = items[items.length - 1]
  const current = document.activeElement
  if (event.shiftKey && (current === first || !dialog.contains(current))) {
    event.preventDefault()
    last.focus()
    return true
  }
  if (!event.shiftKey && (current === last || !dialog.contains(current))) {
    event.preventDefault()
    first.focus()
    return true
  }
  return false
}
