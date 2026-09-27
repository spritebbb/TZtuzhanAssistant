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

const apiFetchMock = vi.hoisted(() =>
  vi.fn(async (_p: string, _opts?: { method?: string; body?: string }) => ({
    ok: true,
    json: async () => ({}),
  })),
)

vi.mock('../../api', () => ({
  apiFetch: apiFetchMock,
}))
vi.mock('../../api/encryption', () => ({
  getEncryptionStatus: vi.fn(async () => ({ enabled: false })),
  enableEncryption: vi.fn(),
  cleanupPlaintext: vi.fn(),
}))
vi.mock('../../api/backup', () => ({
  getBackupStatus: vi.fn(async () => ({
    ok: true,
    has_backup: true,
    encrypted_mode: false,
    age_days: 0.1,
    verify: 'pass',
    file_count: 42,
  })),
  runBackupNow: vi.fn(async () => 'periodic-test-0001'),
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

  it('NP-07：单价输入回显配置值，保存时随 body 提交', async () => {
    apiFetchMock.mockImplementation(async (path: string, opts?: { method?: string; body?: string }) => {
      if (path === '/api/config' && !opts?.method) {
        return {
          ok: true,
          json: async () => ({
            ok: true,
            config: {
              llm_base_url: 'https://api.deepseek.com/v1',
              llm_model: 'deepseek-chat',
              llm_price_input_per_mtok: 2,
              llm_price_output_per_mtok: 8,
            },
          }),
        }
      }
      return { ok: true, json: async () => ({ ok: true }) }
    })

    const wrapper = mount(
      SettingsPanel,
      { props: { show: false, personaName: '菟菚' }, global: { stubs: { teleport: true } } },
    )
    await wrapper.setProps({ show: true })
    await flushPromises()

    const inputField = wrapper.get('input[aria-label="输入单价（元每百万token）"]')
    const outputField = wrapper.get('input[aria-label="输出单价（元每百万token）"]')
    expect((inputField.element as HTMLInputElement).value).toBe('2')
    expect((outputField.element as HTMLInputElement).value).toBe('8')

    await inputField.setValue('3.5')
    // 触发保存（按文本精确定位，面板内其他区域也有 .btn 按钮）
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    expect(saveBtn).toBeTruthy()
    await saveBtn!.trigger('click')
    await flushPromises()

    const postCall = apiFetchMock.mock.calls.find(
      (c) => c[0] === '/api/config' && c[1]?.method === 'POST',
    )
    expect(postCall).toBeTruthy()
    const body = JSON.parse(String(postCall![1]!.body)) as Record<string, string>
    expect(body.llm_price_input_per_mtok).toBe('3.5')
    expect(body.llm_price_output_per_mtok).toBe('8')
  })

  it('NP-12：打开设置展示备份状态（校验通过）；立即备份后状态刷新', async () => {
    const backupMod = (await import('../../api/backup')) as unknown as {
      getBackupStatus: ReturnType<typeof vi.fn>
      runBackupNow: ReturnType<typeof vi.fn>
    }
    const { getBackupStatus, runBackupNow } = backupMod

    const wrapper = mount(
      SettingsPanel,
      { props: { show: false, personaName: '菟菚' }, global: { stubs: { teleport: true } } },
    )
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(getBackupStatus).toHaveBeenCalled()
    expect(wrapper.text()).toContain('最新备份校验通过')

    const backupBtn = wrapper.findAll('button').find((b) => b.text() === '立即备份')
    expect(backupBtn).toBeTruthy()
    await backupBtn!.trigger('click')
    await flushPromises()
    expect(runBackupNow).toHaveBeenCalled()
    // 备份成功后会刷新状态（真实场景读到新备份）——最终行回到校验通过
    expect(wrapper.text()).toContain('最新备份校验通过')
  })

  it('NP-12：超 3 天未备份的黄色提醒（stale 样式）', async () => {
    const { getBackupStatus } = (await import('../../api/backup')) as unknown as {
      getBackupStatus: ReturnType<typeof vi.fn>
    }
    getBackupStatus.mockResolvedValue({
      ok: true,
      has_backup: true,
      encrypted_mode: false,
      age_days: 5.2,
      verify: 'pass',
      file_count: 40,
    })

    const wrapper = mount(
      SettingsPanel,
      { props: { show: false, personaName: '菟菚' }, global: { stubs: { teleport: true } } },
    )
    await wrapper.setProps({ show: true })
    await flushPromises()

    const line = wrapper.get('[role="status"]')
    expect(line.text()).toContain('5 天前')
    expect(line.attributes('style')).toContain('#d9a441')
  })
})
