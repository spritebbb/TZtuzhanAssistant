import { computed, readonly, ref } from 'vue'
import { apiFetch } from '../api'

export type Presence = 'home' | 'mobile' | 'announced_offline' | 'rest' | 'focus'
export interface VisualState {
  persona_id: string
  revision: number
  mood_label: string
  bond_label: string
  energy_band: 'low' | 'medium' | 'high'
  activity_kind: string
  presence: Presence
  quiet: boolean
  reduced_motion: boolean
  source_time: string
}

const state = ref<VisualState>({ persona_id: '', revision: 0, mood_label: '', bond_label: '', energy_band: 'high', activity_kind: '', presence: 'home', quiet: false, reduced_motion: false, source_time: '' })
const systemReduced = ref(false)
const recentEvents = ref<Array<{ date: string; description: string }>>([])
let requestSerial = 0
let pending: { persona: string; promise: Promise<void> } | null = null
let timer: ReturnType<typeof setInterval> | null = null
let media: MediaQueryList | null = null
let mediaListener: (() => void) | null = null

function valid(value: unknown): value is VisualState {
  if (!value || typeof value !== 'object') return false
  const v = value as Partial<VisualState>
  return typeof v.persona_id === 'string' && Number.isFinite(v.revision)
    && ['home', 'mobile', 'announced_offline', 'rest', 'focus'].includes(String(v.presence))
}

export function refreshVisualState(persona = ''): Promise<void> {
  if (pending?.persona === persona) return pending.promise
  const serial = ++requestSerial
  const promise = apiFetch('/api/presence').then(async response => {
    if (!response.ok) throw new Error('presence unavailable')
    const data = await response.json()
    const next = data.visual_state
    if (serial !== requestSerial || !valid(next) || (persona && next.persona_id !== persona)) return
    if (next.persona_id === state.value.persona_id && next.revision < state.value.revision) return
    state.value = next
    if (Array.isArray(data.recent_events)) recentEvents.value = data.recent_events
  }).finally(() => { if (pending?.promise === promise) pending = null })
  pending = { persona, promise }
  return promise
}

export function switchVisualPersona(persona: string) {
  requestSerial += 1
  state.value = { ...state.value, persona_id: persona, revision: 0, source_time: '' }
  return refreshVisualState(persona)
}

export function startVisualState(persona: () => string) {
  if (timer) return
  if (typeof window !== 'undefined' && window.matchMedia) {
    media = window.matchMedia('(prefers-reduced-motion: reduce)')
    mediaListener = () => { systemReduced.value = Boolean(media?.matches) }
    mediaListener()
    media.addEventListener?.('change', mediaListener)
  }
  void refreshVisualState(persona()).catch(() => {})
  timer = setInterval(() => void refreshVisualState(persona()).catch(() => {}), 30_000)
}

export function stopVisualState() {
  requestSerial += 1
  if (timer) clearInterval(timer)
  timer = null
  if (media && mediaListener) media.removeEventListener?.('change', mediaListener)
  media = null
  mediaListener = null
  pending = null
}

export const visualState = readonly(state)
export const visualRecentEvents = readonly(recentEvents)
export const visualQuiet = computed(() => state.value.quiet || state.value.reduced_motion || systemReduced.value)
