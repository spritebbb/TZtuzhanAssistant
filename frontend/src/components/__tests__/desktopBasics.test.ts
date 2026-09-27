// NP-10 桌面四件套回归：设置页「桌面」区（仅桌面壳渲染）——
// 置顶/自启开关回显真实状态并经桥切换；热键下拉来自候选表且保存经桥。
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const electronBridge = vi.hoisted(() => ({
  getBackendUrl: vi.fn(async () => 'http://127.0.0.1:8801'),
  getVersion: vi.fn(async () => 'test'),
  notify: vi.fn(async () => true),
  focusWindow: vi.fn(async () => true),
  setActiveSession: vi.fn(async () => true),
  onInitiativeMessage: vi.fn(() => () => undefined),
  setAlwaysOnTop: vi.fn(async (on: boolean) => on),
  getAlwaysOnTop: vi.fn(async () => true),
  setLaunchAtLogin: vi.fn(async (on: boolean) => on),
  getLaunchAtLogin: vi.fn(async () => false),
  setMainHotkey: vi.fn(async (k: string) => k !== 'Ctrl+Alt+Z'),
  getMainHotkey: vi.fn(async () => 'Alt+Shift+T'),
  getHotkeyChoices: vi.fn(async () => ['Alt+Shift+T', 'Ctrl+Alt+Z', 'Alt+Shift+Q']),
}))

vi.mock('../../api', () => ({
  apiFetch: vi.fn(async () => ({ ok: true, json: async () => ({}) })),
}))
vi.mock('../../api/encryption', () => ({
  getEncryptionStatus: vi.fn(async () => ({ enabled: false })),
  enableEncryption: vi.fn(),
  cleanupPlaintext: vi.fn(),
}))
vi.mock('../../api/backup', () => ({
  getBackupStatus: vi.fn(async () => ({ ok: true, has_backup: false })),
  runBackupNow: vi.fn(async () => 'x'),
}))
vi.mock('../../utils/tts', () => ({
  getTtsAutoPlay: vi.fn(() => false),
  setTtsAutoPlay: vi.fn(),
  stopTts: vi.fn(),
}))

import SettingsPanel from '../SettingsPanel.vue'

describe('SettingsPanel 桌面四件套（NP-10）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    ;(window as unknown as Record<string, unknown>).electronAPI = electronBridge
  })
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).electronAPI
  })

  function mountPanel() {
    return mount(
      SettingsPanel,
      { props: { show: false, personaName: '菟菚' }, global: { stubs: { teleport: true } } },
    )
  }

  it('桌面壳：桌面区渲染，置顶/自启/热键回显真实状态', async () => {
    const wrapper = mountPanel()
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(electronBridge.getAlwaysOnTop).toHaveBeenCalled()
    expect((wrapper.get('input[aria-label="窗口置顶"]').element as HTMLInputElement).checked).toBe(true)
    expect((wrapper.get('input[aria-label="开机自启"]').element as HTMLInputElement).checked).toBe(false)
    const select = wrapper.get('select[aria-label="全局召唤快捷键"]')
    expect((select.element as HTMLSelectElement).value).toBe('Alt+Shift+T')
    expect(wrapper.findAll('option').map((o) => o.text())).toContain('Ctrl+Alt+Z')
  })

  it('切换置顶经桥并更新回显；热键保存失败提示占用回退', async () => {
    const wrapper = mountPanel()
    await wrapper.setProps({ show: true })
    await flushPromises()

    await wrapper.get('input[aria-label="窗口置顶"]').setValue(false)
    await flushPromises()
    expect(electronBridge.setAlwaysOnTop).toHaveBeenCalledWith(false)

    const select = wrapper.get('select[aria-label="全局召唤快捷键"]')
    await select.setValue('Ctrl+Alt+Z') // mock：该键返回 false（被占用）
    await select.trigger('change')
    await flushPromises()
    expect(electronBridge.setMainHotkey).toHaveBeenCalledWith('Ctrl+Alt+Z')
    expect(wrapper.text()).toContain('占用')
  })

  it('网页形态（无 electronAPI）：桌面区整体不渲染', async () => {
    delete (window as unknown as Record<string, unknown>).electronAPI
    const wrapper = mountPanel()
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(wrapper.find('input[aria-label="窗口置顶"]').exists()).toBe(false)
    expect(wrapper.find('select[aria-label="全局召唤快捷键"]').exists()).toBe(false)
  })
})
