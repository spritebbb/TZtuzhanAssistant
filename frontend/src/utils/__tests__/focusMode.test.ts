import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { FocusSession } from '../../api/focus'
import {
  cancelFocus,
  completeFocus,
  getCurrentFocus,
  pauseFocus,
  resumeFocus,
  startFocus,
} from '../../api/focus'
import { useFocusMode } from '../focusMode'

vi.mock('../../api/focus', () => ({
  cancelFocus: vi.fn(),
  completeFocus: vi.fn(),
  getCurrentFocus: vi.fn(),
  pauseFocus: vi.fn(),
  resumeFocus: vi.fn(),
  startFocus: vi.fn(),
}))

const mockedCurrent = vi.mocked(getCurrentFocus)
const mockedStart = vi.mocked(startFocus)
const mockedPause = vi.mocked(pauseFocus)
const mockedResume = vi.mocked(resumeFocus)
const mockedComplete = vi.mocked(completeFocus)
const mockedCancel = vi.mocked(cancelFocus)

function session(overrides: Partial<FocusSession> = {}): FocusSession {
  return {
    id: 5,
    kind: 'focus',
    title: '专注 25 分钟',
    status: 'active',
    planned_minutes: 25,
    remaining_seconds: 1500,
    elapsed_seconds: 0,
    ends_at: new Date(Date.now() + 1500_000).toISOString(),
    created_at: '2026-09-05T20:00:00',
    updated_at: '2026-09-05T20:00:00',
    completed_at: null,
    ...overrides,
  }
}

describe('useFocusMode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.classList.remove('focus-mode')
  })

  it('恢复进行中的专注并挂上安静模式 class', async () => {
    mockedCurrent.mockResolvedValue({ focus: session(), justFinished: false })
    const mode = useFocusMode()
    await mode.refresh()

    expect(mode.current.value?.id).toBe(5)
    expect(mode.active.value).toBe(true)
    expect(mode.remainingSec.value).toBeGreaterThan(1400)
    expect(document.body.classList.contains('focus-mode')).toBe(true)
  })

  it('已结束的会话不占当前槽位，安静模式解除', async () => {
    mockedCurrent.mockResolvedValue({ focus: session({ status: 'completed' }), justFinished: true })
    const mode = useFocusMode()
    await mode.refresh()

    expect(mode.current.value).toBeNull()
    expect(document.body.classList.contains('focus-mode')).toBe(false)
  })

  it('完整状态机动作：开始 → 暂停冻结 → 继续 → 中断', async () => {
    mockedCurrent.mockResolvedValue({ focus: null, justFinished: false })
    const mode = useFocusMode()
    await mode.refresh()

    mockedStart.mockResolvedValue(session())
    await mode.startSession(25)
    expect(mockedStart).toHaveBeenCalledWith(25)
    expect(mode.active.value).toBe(true)

    mockedPause.mockResolvedValue(session({ status: 'paused', ends_at: null, remaining_seconds: 1200 }))
    await mode.pauseSession()
    expect(mode.paused.value).toBe(true)
    expect(mode.remainingSec.value).toBe(1200)
    expect(document.body.classList.contains('focus-mode')).toBe(false)

    mockedResume.mockResolvedValue(session({ remaining_seconds: 1200 }))
    await mode.resumeSession()
    expect(mode.active.value).toBe(true)

    mockedCancel.mockResolvedValue(session({ status: 'cancelled' }))
    await mode.cancelSession()
    expect(mode.current.value).toBeNull()
  })

  it('提前结束（完成）后槽位清空', async () => {
    mockedCurrent.mockResolvedValue({ focus: session(), justFinished: false })
    const mode = useFocusMode()
    await mode.refresh()

    mockedComplete.mockResolvedValue(session({ status: 'completed', completed_at: '2026-09-05T20:25:00' }))
    await mode.completeSession()
    expect(mockedComplete).toHaveBeenCalledWith(5)
    expect(mode.current.value).toBeNull()
    expect(document.body.classList.contains('focus-mode')).toBe(false)
  })
})
