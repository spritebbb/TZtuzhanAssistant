import { beforeEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../../api'
import { refreshVisualState, stopVisualState, switchVisualPersona, visualState } from '../visualState'

vi.mock('../../api', () => ({ apiFetch: vi.fn() }))
const view = (persona: string, revision: number) => ({
  persona_id: persona, revision, mood_label: '平静', bond_label: '熟悉', energy_band: 'high',
  activity_kind: 'reading', presence: 'home', quiet: false, reduced_motion: false, source_time: '2026-09-09T00:00:00',
})
const response = (value: object) => ({ ok: true, json: async () => ({ visual_state: value, recent_events: [] }) }) as Response

beforeEach(() => { stopVisualState(); vi.mocked(apiFetch).mockReset() })

it('deduplicates concurrent reads and rejects older revisions', async () => {
  vi.mocked(apiFetch).mockResolvedValue(response(view('one', 20)))
  const a = refreshVisualState('one')
  const b = refreshVisualState('one')
  expect(apiFetch).toHaveBeenCalledTimes(1)
  await Promise.all([a, b])
  vi.mocked(apiFetch).mockResolvedValue(response(view('one', 10)))
  await refreshVisualState('one')
  expect(visualState.value.revision).toBe(20)
})

it('drops a late response after persona switch', async () => {
  let release!: (value: Response) => void
  vi.mocked(apiFetch).mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  const old = refreshVisualState('old')
  vi.mocked(apiFetch).mockResolvedValueOnce(response(view('new', 3)))
  await switchVisualPersona('new')
  release(response(view('old', 99)))
  await old
  expect(visualState.value.persona_id).toBe('new')
  expect(visualState.value.revision).toBe(3)
})

it('switches visible persona synchronously without waiting for the network', () => {
  vi.mocked(apiFetch).mockImplementation(() => new Promise(() => {}))
  const pending = switchVisualPersona('instant')
  expect(visualState.value.persona_id).toBe('instant')
  expect(visualState.value.revision).toBe(0)
  expect(apiFetch).toHaveBeenCalledTimes(1)
  expect(pending).toBeInstanceOf(Promise)
})
