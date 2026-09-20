/**
 * L10：桌面宠物避让/定位的纯逻辑（不 import electron，Vitest 直接覆盖）。
 *
 * 约定：所有坐标都是 DIP（device-independent pixels）；物理像素换算只发生一次
 * （toDip），不能在 clamp 与保存之间重复乘除 scaleFactor。
 */

export interface Rect { x: number; y: number; width: number; height: number }

export interface DisplayLike {
  id: string
  workArea: Rect
  scaleFactor: number
}

/** 物理像素矩形 → DIP 矩形（只除一次）。 */
export function toDip(rect: Rect, scaleFactor: number): Rect {
  const s = scaleFactor > 0 ? scaleFactor : 1
  return {
    x: rect.x / s,
    y: rect.y / s,
    width: rect.width / s,
    height: rect.height / s,
  }
}

function center(r: Rect): { x: number; y: number } {
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
}

function distance(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

/** clamp 窗口矩形到「最近的显示器工作区」（双屏负坐标下选有交集/中心距最小者）。 */
export function clampToWorkArea(win: Rect, displays: DisplayLike[]): Rect {
  if (displays.length === 0) return win
  const wc = center(win)
  let best = displays[0]
  let bestScore = Number.POSITIVE_INFINITY
  for (const d of displays) {
    const wa = d.workArea
    // 有交集的显示器优先（交集面积 > 0 视为强命中）
    const overlapW = Math.min(win.x + win.width, wa.x + wa.width) - Math.max(win.x, wa.x)
    const overlapH = Math.min(win.y + win.height, wa.y + wa.height) - Math.max(win.y, wa.y)
    const score = overlapW > 0 && overlapH > 0
      ? -Math.min(overlapW, overlapH) // 强命中：负分，交集越大越优先
      : distance(wc, center(wa))
    if (score < bestScore) {
      bestScore = score
      best = d
    }
  }
  const wa = best.workArea
  return {
    width: win.width,
    height: win.height,
    x: Math.min(Math.max(win.x, wa.x), wa.x + wa.width - win.width),
    y: Math.min(Math.max(win.y, wa.y), wa.y + wa.height - win.height),
  }
}

export interface PetDisplayPrefs {
  display_id?: string
  x: number
  y: number
  scale_factor?: number
}

/** 跨机/拔插后恢复位置：先按 display_id，其次按坐标落在哪个工作区，兜底第一个。 */
export function pickDisplayForRestore(
  prefs: PetDisplayPrefs | null,
  displays: DisplayLike[],
): DisplayLike {
  if (displays.length === 0) throw new Error('没有可用显示器')
  if (prefs?.display_id) {
    const byId = displays.find((d) => d.id === prefs.display_id)
    if (byId) return byId
  }
  if (prefs) {
    const containing = displays.find(
      (d) =>
        prefs.x >= d.workArea.x && prefs.x <= d.workArea.x + d.workArea.width &&
        prefs.y >= d.workArea.y && prefs.y <= d.workArea.y + d.workArea.height,
    )
    if (containing) return containing
  }
  return displays[0]
}

export interface ForegroundProbeResult {
  /** 前台窗口是否处于全屏（helper 只返回这个与 displayId，不抓屏不读内容） */
  fullscreen: boolean
  displayId?: string
}

/**
 * 全屏避让决策：
 * - helper 已配置但探测失败 → 保守隐藏（宁可看不见，不挡用户看片）；
 * - helper 未配置（骨架/mock 模式）→ 不隐藏，由上层如实标注「未实装」；
 * - 命中全屏或用户排除进程 → 隐藏，并给出所在显示器以便恢复。
 */
export function avoidanceDecision(input: {
  helperConfigured: boolean
  probe: ForegroundProbeResult | null
  probeError: boolean
  userExcluded: boolean
}): { hide: boolean; displayId?: string } {
  if (input.userExcluded) return { hide: true }
  if (!input.helperConfigured) return { hide: false }
  if (input.probeError || !input.probe) return { hide: true }
  if (input.probe.fullscreen) return { hide: true, displayId: input.probe.displayId }
  return { hide: false }
}

/** 避让轮询间隔：活跃（可见/最近有变化）1s，空闲降频到 5s。 */
export function pollIntervalMs(active: boolean): number {
  return active ? 1000 : 5000
}
