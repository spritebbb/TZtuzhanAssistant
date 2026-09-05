import { computed, ref } from 'vue'

import {
  cancelFocus,
  completeFocus,
  getCurrentFocus,
  pauseFocus,
  resumeFocus,
  startFocus,
  type FocusSession,
} from '../api/focus'

/**
 * M3.2 专注陪伴的全局状态（单例）。
 *
 * - 每 30 秒与后端对一次表（自然到点由后端惰性结算），每秒本地倒计时；
 * - 专注进行中给 body 挂 `focus-mode` class，前端据此减少动画与视觉刺激；
 * - 面板关闭后状态仍在，刷新/重开应用后从后端恢复（活动状态与聊天解耦）。
 */

const current = ref<FocusSession | null>(null)
const remainingSec = ref(0)
const busy = ref(false)
const loaded = ref(false)

let pollTimer: ReturnType<typeof setInterval> | null = null
let tickTimer: ReturnType<typeof setInterval> | null = null

const active = computed(() => current.value?.status === 'active')
const paused = computed(() => current.value?.status === 'paused')

function applyBodyClass() {
  if (typeof document === 'undefined') return
  document.body.classList.toggle('focus-mode', active.value)
}

function syncRemaining(session: FocusSession | null) {
  if (session && session.status === 'active' && session.ends_at) {
    const end = new Date(session.ends_at).getTime()
    remainingSec.value = Math.max(0, Math.round((end - Date.now()) / 1000))
  } else if (session && session.status === 'paused') {
    remainingSec.value = session.remaining_seconds
  } else {
    remainingSec.value = 0
  }
}

function setCurrent(session: FocusSession | null) {
  // 已结束（完成/取消）的会话不再占据「当前」槽位
  current.value = session && (session.status === 'active' || session.status === 'paused') ? session : null
  syncRemaining(current.value)
  applyBodyClass()
}

async function refresh() {
  try {
    const { focus } = await getCurrentFocus()
    setCurrent(focus)
  } catch {
    // 读取失败保持现状，下一轮对表再试
  } finally {
    loaded.value = true
  }
}

async function run(action: () => Promise<FocusSession>) {
  if (busy.value) return
  busy.value = true
  try {
    setCurrent(await action())
  } finally {
    busy.value = false
  }
}

export function useFocusMode() {
  function start() {
    if (pollTimer) return
    void refresh()
    pollTimer = setInterval(() => void refresh(), 30_000)
    tickTimer = setInterval(() => {
      if (active.value && remainingSec.value > 0) {
        remainingSec.value -= 1
        if (remainingSec.value === 0) void refresh() // 本地数到 0，立刻找后端结算
      }
    }, 1_000)
  }

  function stop() {
    if (pollTimer) clearInterval(pollTimer)
    if (tickTimer) clearInterval(tickTimer)
    pollTimer = null
    tickTimer = null
  }

  return {
    current,
    remainingSec,
    busy,
    loaded,
    active,
    paused,
    start,
    stop,
    refresh,
    startSession: (minutes: 25 | 50) => run(() => startFocus(minutes)),
    pauseSession: () => current.value && run(() => pauseFocus(current.value!.id)),
    resumeSession: () => current.value && run(() => resumeFocus(current.value!.id)),
    completeSession: () => current.value && run(() => completeFocus(current.value!.id)),
    cancelSession: () => current.value && run(() => cancelFocus(current.value!.id)),
  }
}
