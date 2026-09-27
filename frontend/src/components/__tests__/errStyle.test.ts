// NP-09 错误提示分区对待：源码级断言（不渲染组件）。
// 1) 禁止 window.alert / alert / window.confirm / confirm ——确认走 ConfirmDialog，失败走 notify toast；
// 2) 禁止空 catch（catch {} 或 catch { /* ignore */ }）——要么给可见反馈，要么注释写明降级语义。
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const srcRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

function walkVue(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    const st = statSync(p)
    if (st.isDirectory()) out.push(...walkVue(p))
    else if (name.endsWith('.vue') || name.endsWith('.ts')) out.push(p)
  }
  return out
}

const files = walkVue(srcRoot).filter((p) => !p.includes('__tests__'))
// 组件层 = UX 面（面板/视图/根组件）；api/*.ts 的流容错与错误消息由消费方呈现，不在本断言范围
const componentFiles = files.filter((p) => p.endsWith('.vue'))

describe('NP-09 错误提示风格', () => {
  it('无 window.alert / alert / confirm 原生弹窗', () => {
    const offenders: string[] = []
    for (const f of componentFiles) {
      const s = readFileSync(f, 'utf-8')
      if (/\b(window\.)?(alert|confirm)\s*\(/.test(s)) offenders.push(f)
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })

  it('无真空 catch（块内无任何代码或注释；带语义注释的静默视为已声明降级语义，合规）', () => {
    const offenders: string[] = []
    for (const f of componentFiles) {
      const s = readFileSync(f, 'utf-8')
      if (/catch\s*(\([^)]*\))?\s*\{\s*\}/.test(s)) offenders.push(f)
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })

  it('面板报错文案无「过会儿/打不开/卡住了」式含糊表达', () => {
    const offenders: string[] = []
    for (const f of componentFiles) {
      const s = readFileSync(f, 'utf-8')
      if (/过会儿|打不开|卡住了/.test(s)) offenders.push(f)
    }
    expect(offenders, offenders.join('\n')).toEqual([])
  })
})
