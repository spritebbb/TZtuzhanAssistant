/**
 * L10：桌面宠物窗口（Electron 胶水层）。
 *
 * 安全边界（技术指导 §16 L10）：
 * - 透明 frameless 独立窗口；contextIsolation=true / nodeIntegration=false；
 * - 固定本地页面（pet.html，随包由后端静态目录/dev server 提供）；
 * - IPC 白名单只有 pet:drag / pet:close / pet:toggleIgnoreMouse / pet:toggle；
 * - 默认不置顶、showInactive 不抢焦点；透明区点击穿透（forward: true）；
 * - 屏幕拔插/DPI 变化按 screen.workArea 重新 clamp（DIP 语义，坐标不二次缩放）；
 * - 全屏避让走 displayAvoidance 的注入式 probe（骨架未配 helper → mock 模式不隐藏）；
 * - 单实例 + 全量清理：close 释放所有订阅与定时器。
 */
import { BrowserWindow, ipcMain, screen } from 'electron'

import {
  type DisplayLike,
  type PetDisplayPrefs,
  avoidanceDecision,
  clampToWorkArea,
  pickDisplayForRestore,
  pollIntervalMs,
} from '../src/desktop/displayAvoidance'
import { PetWindowState } from '../src/desktop/petWindowState'

const PET_SIZE = { width: 260, height: 360 }
/** IPC 白名单：renderer 只能发这四个通道，其余一律拒绝。 */
const PET_IPC_CHANNELS = ['pet:drag', 'pet:close', 'pet:toggleIgnoreMouse', 'pet:toggle'] as const

export interface PetWindowDeps {
  preloadPath: () => string
  resolveUrl: () => string
  loadPrefs: () => PetDisplayPrefs | null
  savePrefs: (prefs: PetDisplayPrefs) => void
  /** 前台全屏探测（L10：经后端 /api/desktop/fullscreen；返回 null=探测失败→保守隐藏） */
  foregroundProbe?: (() => Promise<{ fullscreen: boolean; displayId?: string } | null>) | null
  userExcluded?: () => boolean
}

export interface PetWindowManager {
  toggle(): Promise<boolean>
  open(): Promise<boolean>
  close(): void
  isOpen(): boolean
}

export function createPetWindowManager(deps: PetWindowDeps): PetWindowManager {
  const state = new PetWindowState()
  let win: BrowserWindow | null = null
  let avoidanceTimer: NodeJS.Timeout | null = null

  function displays(): DisplayLike[] {
    return screen.getAllDisplays().map((d) => ({
      id: String(d.id),
      workArea: {
        x: d.workArea.x,
        y: d.workArea.y,
        width: d.workArea.width,
        height: d.workArea.height,
      },
      scaleFactor: d.scaleFactor,
    }))
  }

  function currentPosition(): { x: number; y: number } {
    const pos = win?.getPosition() ?? [0, 0]
    return { x: pos[0], y: pos[1] }
  }

  function reclamp(): void {
    if (!win) return
    const p = currentPosition()
    const clamped = clampToWorkArea(
      { x: p.x, y: p.y, ...PET_SIZE },
      displays(),
    )
    win.setPosition(Math.round(clamped.x), Math.round(clamped.y), false)
  }

  function saveCurrentPrefs(): void {
    if (!win) return
    const p = currentPosition()
    const nearest = pickDisplayForRestore({ x: p.x, y: p.y }, displays())
    deps.savePrefs({
      display_id: nearest.id,
      x: p.x,
      y: p.y,
      scale_factor: nearest.scaleFactor,
    })
  }

  function startAvoidancePolling(): void {
    if (!deps.foregroundProbe) return // 未配探测：不启用避让（保留窗口常显）
    const probe = deps.foregroundProbe
    let probeError = false
    const tick = async () => {
      if (!win || win.isDestroyed()) return
      let result: { fullscreen: boolean; displayId?: string } | null = null
      try {
        result = await probe()
        probeError = false
      } catch {
        probeError = true
      }
      if (!win || win.isDestroyed()) return
      const decision = avoidanceDecision({
        helperConfigured: true,
        probe: result,
        probeError,
        userExcluded: Boolean(deps.userExcluded?.()),
      })
      if (decision.hide && win.isVisible()) {
        win.hide() // 不改焦点：隐藏自己，不动别的窗口
      } else if (!decision.hide && !win.isVisible() && !win.isDestroyed()) {
        win.showInactive()
      }
      if (avoidanceTimer) clearTimeout(avoidanceTimer)
      avoidanceTimer = setTimeout(tick, pollIntervalMs(win.isVisible()))
    }
    void tick()
  }

  async function open(): Promise<boolean> {
    if (!state.beginOpen()) return false // 单实例闸
    const prefs = deps.loadPrefs()
    const display = pickDisplayForRestore(prefs, displays())
    const clamped = clampToWorkArea(
      { x: prefs?.x ?? display.workArea.x + 40, y: prefs?.y ?? display.workArea.y + 40, ...PET_SIZE },
      [display],
    )
    win = new BrowserWindow({
      ...PET_SIZE,
      transparent: true,
      frame: false,
      resizable: false,
      skipTaskbar: true,
      alwaysOnTop: false, // 默认不置顶；用户偏好后续可配
      show: false,
      webPreferences: {
        preload: deps.preloadPath(),
        contextIsolation: true,
        nodeIntegration: false,
      },
    })
    win.setVisibleOnAllWorkspaces?.(false)
    await win.loadURL(deps.resolveUrl())
    win.setPosition(Math.round(clamped.x), Math.round(clamped.y), false)
    win.showInactive() // 不抢焦点
    win.on('moved', saveCurrentPrefs)
    win.on('closed', () => {
      win = null
      state.close()
    })
    state.onCleanup(() => {
      win?.removeListener('moved', saveCurrentPrefs)
    })
    // 屏幕拔插/旋转/DPI 变化：按工作区重新 clamp（DIP 语义）
    const onMetrics = () => reclamp()
    screen.on('display-metrics-changed', onMetrics)
    screen.on('display-removed', onMetrics)
    state.onCleanup(() => {
      screen.removeListener('display-metrics-changed', onMetrics)
      screen.removeListener('display-removed', onMetrics)
    })
    state.onCleanup(() => {
      if (avoidanceTimer) clearTimeout(avoidanceTimer)
      avoidanceTimer = null
    })
    state.onCleanup(() => {
      if (win && !win.isDestroyed()) win.destroy()
      win = null
    })
    startAvoidancePolling()
    return true
  }

  function close(): void {
    state.close() // 触发全部清理（destroy → closed 回调里的 state.close 幂等）
    win = null
  }

  function attachIpc(): void {
    ipcMain.on('pet:drag', (_e, delta: { dx?: number; dy?: number }) => {
      if (!win) return
      const p = currentPosition()
      const moved = clampToWorkArea(
        { x: p.x + Number(delta?.dx || 0), y: p.y + Number(delta?.dy || 0), ...PET_SIZE },
        displays(),
      )
      win.setPosition(Math.round(moved.x), Math.round(moved.y), false)
    })
    ipcMain.on('pet:close', () => close())
    ipcMain.on('pet:toggleIgnoreMouse', (_e, ignore: boolean) => {
      // forward: true 让穿透区域仍能收到鼠标事件用于恢复交互
      win?.setIgnoreMouseEvents(Boolean(ignore), { forward: true })
    })
  }

  attachIpc()

  return {
    isOpen: () => state.open,
    open,
    close,
    async toggle(): Promise<boolean> {
      if (state.open) {
        close()
        return false
      }
      return open()
    },
  }
}

export { PET_IPC_CHANNELS }
