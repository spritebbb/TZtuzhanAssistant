// 沉浸模式回归：偏好读写与事件广播、侧栏心情卡片的数值隐藏/叙事化、
// 设置页开关即改即生效。
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { IMMERSION_EVENT, isImmersionMode, setImmersionMode } from '../../utils/immersion'

const mockListArchives = vi.hoisted(() => vi.fn(async () => []))
const mockGetArchive = vi.hoisted(() => vi.fn(async () => null))
const mockSearchArchives = vi.hoisted(() => vi.fn(async () => []))
const mockRename = vi.hoisted(() => vi.fn(async () => true))

vi.mock('../../api/sessions', () => ({
  CURRENT_SESSION_ID: 'current',
  listArchives: mockListArchives,
  getArchive: mockGetArchive,
  searchArchives: mockSearchArchives,
  renameSession: mockRename,
}))
vi.mock('../../api', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, json: async () => ({ mood: { value: 72, label: '开心', emoji: '😄' } }) })),
  getApiUrl: (p: string) => p,
}))
vi.mock('../../utils/images', () => ({
  resolveImageSrc: (p: string) => p,
}))

import SessionList from '../SessionList.vue'

describe('沉浸模式偏好（utils）', () => {
  afterEach(() => {
    localStorage.clear()
  })

  it('默认关闭；开启后持久化并广播事件', () => {
    expect(isImmersionMode()).toBe(false)
    const spy = vi.fn()
    window.addEventListener(IMMERSION_EVENT, spy)
    setImmersionMode(true)
    window.removeEventListener(IMMERSION_EVENT, spy)
    expect(isImmersionMode()).toBe(true)
    expect(spy).toHaveBeenCalledTimes(1)
    setImmersionMode(false)
  })
})

describe('侧栏心情卡片沉浸态', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })
  afterEach(() => {
    localStorage.clear()
  })

  async function mountList() {
    const wrapper = mount(SessionList, {
      props: { open: true, currentId: null, personaName: '菟菚' },
    })
    await flushPromises()
    return wrapper
  }

  it('默认显示数值与进度条', async () => {
    const wrapper = await mountList()
    expect(wrapper.get('.moodnum').text()).toBe('72')
    expect(wrapper.find('.moodbar').exists()).toBe(true)
  })

  it('开启沉浸模式：数值与进度条隐藏，标签保留并带「沉浸中」标记，悬停不再泄露数值', async () => {
    const wrapper = await mountList()
    setImmersionMode(true)
    await flushPromises()

    expect(wrapper.find('.moodnum').exists()).toBe(false)
    expect(wrapper.find('.moodbar').exists()).toBe(false)
    expect(wrapper.get('.moodlabel').text()).toContain('开心')
    expect(wrapper.get('.moodlabel').text()).toContain('沉浸中')
    expect(wrapper.get('.moodcard').attributes('title')).not.toContain('72')

    setImmersionMode(false)
    await flushPromises()
    expect(wrapper.get('.moodnum').text()).toBe('72')
  })
})
