import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(resolve(process.cwd(), 'src/style.css'), 'utf8')

function block(pattern: RegExp): string {
  const match = css.match(pattern)
  if (!match) throw new Error(`missing CSS block: ${pattern}`)
  return match[1]
}

function token(source: string, name: string): string {
  const match = source.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))
  if (!match) throw new Error(`missing hex token: ${name}`)
  return match[1]
}

function luminance(hex: string): number {
  const values = [1, 3, 5].map(offset => Number.parseInt(hex.slice(offset, offset + 2), 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
  return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]
}

function contrast(foreground: string, background: string): number {
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  return (values[0] + 0.05) / (values[1] + 0.05)
}

describe('Q5 theme contrast tokens', () => {
  it('keeps normal dark and light text at WCAG AA contrast', () => {
    const dark = block(/:root\s*\{([\s\S]*?)\n\}/)
    const light = block(/\.theme-light\s*\{([\s\S]*?)\n\}/)
    for (const [name, source] of [['dark', dark], ['light', light]] as const) {
      const background = token(source, 'bg')
      for (const foregroundName of ['text', 'text-dim', 'text-faint']) {
        const ratio = contrast(token(source, foregroundName), background)
        expect(ratio, `${name} ${foregroundName} contrast`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })
})
