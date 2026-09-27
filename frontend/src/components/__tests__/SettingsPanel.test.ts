// NP-06 设置面板宠物开关回显回归：此前 petOn 恒初始化 false，
// 宠物实际开着时开关显示关闭，再点一次会误关。现在打开设置即查询真实状态。
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const petBridge = vi.hoisted(() => ({
  drag: vi.fn(),
  close: vi.fn(),
  toggleIgnoreMouse: vi.fn(),
  togglePet: vi.fn(async () => false),
  getPetState: vi.fn(async () => true),
}))

vi.mock('../../api', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, json: async () => ({}) })),
}))
vi.mock('../../api/encryption', () => ({
  getEncryptionStatus: vi.fn(async () => ({ enabled: false })),
  enableEncryption: vi.fn(),
  cleanupPlaintext: vi.fn(),
}))
vi.mock('../../utils/tts', () => ({
  getTtsAutoPlay: vi.fn(() => false),
  setTtsAutoPlay: vi.fn(),
  stopTts: vi.fn(),
}))

import SettingsPanel from '../SettingsPanel.vue'

describe('SettingsPanel 宠物开关状态回显（NP-06）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    ;(window as unknown as Record<string, unknown>).tuzhanPet = petBridge
  })
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).tuzhanPet
  })

  it('打开设置时宠物开关回显真实状态（getPetState=true → 勾选）', async () => {
    const wrapper = mount(
      SettingsPanel,
      { props: { show: false, personaName: '菟菚' }, global: { stubs: { teleport: true } } },
    )
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(petBridge.getPetState).toHaveBeenCalled()
    const checkbox = wrapper.get('input[aria-label="桌面宠物"]')
    expect((checkbox.element as HTMLInputElement).checked).toBe(true)
  })

  it('getPetState 失败不阻塞设置页打开（保持默认关闭态）', async () => {
    petBridge.getPetState.mockRejectedValue(new Error('ipc boom'))
    const wrapper = mount(
      SettingsPanel,
      { props: { show: false, personaName: '菟菚' }, global: { stubs: { teleport: true } } },
    )
    await wrapper.setProps({ show: true })
    await flushPromises()

    const checkbox = wrapper.get('input[aria-label="桌面宠物"]')
    expect((checkbox.element as HTMLInputElement).checked).toBe(false)
  })
})
