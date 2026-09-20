/**
 * L10：宠物窗口的单实例与资源回收状态机（纯逻辑，Vitest 覆盖）。
 *
 * 规则（技术指导 §16 L10）：
 * - 最多一个 petWindow：beginOpen 在已开时返回 false；
 * - 所有监听器/定时器经 onCleanup 登记，close 时统一释放——快速开关 10 次
 *   后 pendingCleanups 必须为 0（无残留 timer/订阅）；
 * - 开关 off 立即 close。
 */
export class PetWindowState {
  private _open = false
  private closeActions: Array<() => void> = []

  get open(): boolean {
    return this._open
  }

  /** 返回 false 表示已有窗口（单实例闸）。 */
  beginOpen(): boolean {
    if (this._open) return false
    this._open = true
    this.closeActions = []
    return true
  }

  /** 登记需要在关闭时释放的资源（事件订阅、定时器、动画等）。 */
  onCleanup(action: () => void): void {
    if (!this._open) {
      action()
      return
    }
    this.closeActions.push(action)
  }

  close(): void {
    for (const action of this.closeActions) {
      try {
        action()
      } catch {
        // 释放失败不阻断其余清理
      }
    }
    this.closeActions = []
    this._open = false
  }

  /** 测试观察用：尚未释放的清理动作数。 */
  get pendingCleanups(): number {
    return this.closeActions.length
  }
}

/** 快速切换 N 次（toggle 压力路径）：结束时必须回到关闭态且无残留。 */
export function rapidToggle(
  state: PetWindowState,
  open: () => void,
  times: number,
): void {
  for (let i = 0; i < times; i++) {
    open()
    state.close()
  }
}
