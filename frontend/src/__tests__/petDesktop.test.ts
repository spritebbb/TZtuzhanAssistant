import { describe, expect, it } from 'vitest'

import {
  type DisplayLike,
  avoidanceDecision,
  clampToWorkArea,
  pickDisplayForRestore,
  pollIntervalMs,
  toDip,
} from '../desktop/displayAvoidance'
import { PetWindowState, rapidToggle } from '../desktop/petWindowState'

const displays: DisplayLike[] = [
  { id: 'primary', workArea: { x: 0, y: 0, width: 1920, height: 1040 }, scaleFactor: 1 },
  // 双屏负坐标：主屏左侧的副屏（L10 验收点名场景）
  { id: 'left', workArea: { x: -1280, y: 0, width: 1280, height: 1000 }, scaleFactor: 1 },
]

describe('L10 displayAvoidance', () => {
  it('窗口在副屏负坐标内保持不动', () => {
    const win = { x: -1000, y: 200, width: 260, height: 360 }
    expect(clampToWorkArea(win, displays)).toEqual(win)
  })

  it('越界窗口拉回最近工作区（不按物理像素重复缩放）', () => {
    // 副屏 x∈[-1280,0]，窗口 x=-1500 越界 → 拉回 -1280
    const clamped = clampToWorkArea({ x: -1500, y: 500, width: 260, height: 360 }, displays)
    expect(clamped.x).toBe(-1280)
    // y=500+360=860 < 1000 不越界，y 保持
    expect(clamped.y).toBe(500)
  })

  it('掉进缝里的窗口回到交集更大的屏（primary）', () => {
    // 窗口横跨 [-30,230]：与副屏交集 30px、与主屏交集 230px → 归主屏并拉回其工作区
    const clamped = clampToWorkArea({ x: -30, y: 300, width: 260, height: 360 }, displays)
    expect(clamped.x).toBe(0)
  })

  it('toDip 只换算一次（150% DPI）', () => {
    expect(toDip({ x: 3000, y: 1500, width: 300, height: 150 }, 1.5)).toEqual({
      x: 2000, y: 1000, width: 200, height: 100,
    })
  })

  it('恢复位置：display_id 命中 > 坐标所在屏 > 第一个', () => {
    expect(pickDisplayForRestore({ display_id: 'left', x: 0, y: 0 }, displays).id).toBe('left')
    expect(pickDisplayForRestore({ display_id: 'gone', x: -500, y: 10 }, displays).id).toBe('left')
    expect(pickDisplayForRestore(null, displays).id).toBe('primary')
    expect(pickDisplayForRestore({ display_id: 'gone', x: 99999, y: 99999 }, displays).id).toBe('primary')
  })

  it('避让决策：helper 失效保守隐藏；未配置不隐藏；全屏隐藏', () => {
    expect(avoidanceDecision({ helperConfigured: true, probe: null, probeError: true, userExcluded: false })).toEqual({ hide: true })
    expect(avoidanceDecision({ helperConfigured: false, probe: null, probeError: false, userExcluded: false })).toEqual({ hide: false })
    expect(avoidanceDecision({
      helperConfigured: true,
      probe: { fullscreen: true, displayId: 'left' },
      probeError: false,
      userExcluded: false,
    })).toEqual({ hide: true, displayId: 'left' })
    expect(avoidanceDecision({ helperConfigured: true, probe: { fullscreen: false }, probeError: false, userExcluded: true })).toEqual({ hide: true })
  })

  it('轮询降频：活跃 1s / 空闲 5s', () => {
    expect(pollIntervalMs(true)).toBe(1000)
    expect(pollIntervalMs(false)).toBe(5000)
  })
})

describe('L10 petWindowState 单实例与清理', () => {
  it('beginOpen 二次调用返回 false（单实例闸）', () => {
    const state = new PetWindowState()
    expect(state.beginOpen()).toBe(true)
    expect(state.beginOpen()).toBe(false)
  })

  it('close 释放全部登记资源，pendingCleanups 归零', () => {
    const state = new PetWindowState()
    state.beginOpen()
    const released: number[] = []
    state.onCleanup(() => released.push(1))
    state.onCleanup(() => { throw new Error('坏清理不阻断其余') })
    state.onCleanup(() => released.push(2))
    expect(state.pendingCleanups).toBe(3)
    state.close()
    expect(released.sort()).toEqual([1, 2])
    expect(state.pendingCleanups).toBe(0)
    expect(state.open).toBe(false)
  })

  it('未 open 时 onCleanup 立即执行（不积压）', () => {
    const state = new PetWindowState()
    let ran = false
    state.onCleanup(() => { ran = true })
    expect(ran).toBe(true)
    expect(state.pendingCleanups).toBe(0)
  })

  it('快速切换 10 次无残留', () => {
    const state = new PetWindowState()
    let created = 0
    rapidToggle(state, () => {
      if (!state.beginOpen()) return
      created += 1
      state.onCleanup(() => { created -= 1 })
    }, 10)
    expect(created).toBe(0)
    expect(state.open).toBe(false)
  })
})
