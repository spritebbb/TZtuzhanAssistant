// NP-14 桌面感知开关回归：设置页「桌面感知」开关渲染 + 隐私说明文案在场 +
// 保存时以 0/1 提交（后端只接受 0/1）。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiFetchMock = vi.hoisted(() =>
  vi.fn(async (_p: string, opts?: { method?: string; body?: string }) => ({
    ok: true,
    json: async () => {
      if (_p === '/api/config' && !opts?.method) {
        return {
          ok: true,
          config: {
            llm_base_url: 'https://api.deepseek.com/v1',
            llm_model: 'deepseek-chat',
            desktop_awareness: false,
          },
        }
      }
      return { ok: true }
    },
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
  getBackupStatus: vi.fn(async () => ({ ok: true, has_backup: false })),
  runBackupNow: vi.fn(async () => 'x'),
}))
vi.mock('../../utils/tts', () => ({
  getTtsAutoPlay: vi.fn(() => false),
  setTtsAutoPlay: vi.fn(),
  stopTts: vi.fn(),
}))

import SettingsPanel from '../SettingsPanel.vue'

describe('桌面感知开关（NP-14）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  function mountPanel() {
    return mount(
      SettingsPanel,
      { props: { show: false, personaName: '菟菚' }, global: { stubs: { teleport: true } } },
    )
  }

  it('开关渲染 + 隐私说明文案（不看窗口内容）', async () => {
    const wrapper = mountPanel()
    await wrapper.setProps({ show: true })
    await flushPromises()

    const box = wrapper.get('input[aria-label="启用桌面感知"]')
    expect((box.element as HTMLInputElement).checked).toBe(false) // 默认关
    expect(wrapper.text()).toContain('不看窗口内容')
  })

  it('开启后保存以 0/1 提交', async () => {
    const wrapper = mountPanel()
    await wrapper.setProps({ show: true })
    await flushPromises()

    await wrapper.get('input[aria-label="启用桌面感知"]').setValue(true)
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    await saveBtn!.trigger('click')
    await flushPromises()

    const postCall = apiFetchMock.mock.calls.find(
      (c) => c[0] === '/api/config' && c[1]?.method === 'POST',
    )
    expect(postCall).toBeTruthy()
    const body = JSON.parse(String(postCall![1]!.body)) as Record<string, string>
    expect(body.desktop_awareness).toBe('1')
  })
})
