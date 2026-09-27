// NP-06 首次运行向导回归：触发判定、四步流（桌面）/三步流（网页）、
// Key 保存仅 POST 白名单三字段、宠物开关真实状态回显、跳过/完成都落 flag。
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FIRST_RUN_FLAG, shouldShowFirstRun } from '../../utils/firstRun'
import FirstRunWizard from '../FirstRunWizard.vue'

const apiFetchMock = vi.hoisted(() =>
  vi.fn(async (p: string, opts?: { method?: string; body?: string }) => ({
    ok: true,
    json: async () => {
      if (p === '/api/config' && !opts?.method) {
        return {
          ok: true,
          config: {
            llm_base_url: 'https://api.deepseek.com/v1',
            llm_model: 'deepseek-chat',
            llm_api_key_masked: '',
          },
        }
      }
      return { ok: true }
    },
  })),
)

const petBridge = vi.hoisted(() => ({
  drag: vi.fn(),
  close: vi.fn(),
  toggleIgnoreMouse: vi.fn(),
  togglePet: vi.fn(async () => false),
  getPetState: vi.fn(async () => true),
}))

vi.mock('../../api', () => ({
  apiFetch: (p: string, opts?: { method?: string }) => apiFetchMock(p, opts),
}))

function setWindowKeys(
  target: Record<string, unknown>,
): void {
  for (const [k, v] of Object.entries(target)) {
    ;(window as unknown as Record<string, unknown>)[k] = v
  }
}
function delWindowKeys(keys: string[]): void {
  const w = window as unknown as Record<string, unknown>
  for (const k of keys) delete w[k]
}

describe('firstRun 触发判定（NP-06）', () => {
  it('没看过向导且 Key 未配置 → 弹', () => {
    expect(shouldShowFirstRun(null, { config: { llm_api_key_masked: '' } })).toBe(true)
  })
  it('看过向导（flag 在）→ 永不再弹', () => {
    expect(shouldShowFirstRun('1', { config: { llm_api_key_masked: '' } })).toBe(false)
  })
  it('Key 已配置 → 不弹（老用户升级不被打扰）', () => {
    expect(shouldShowFirstRun(null, { config: { llm_api_key_masked: 'sk-12****7890' } })).toBe(false)
  })
  it('响应形态异常 → 保守不弹', () => {
    expect(shouldShowFirstRun(null, null)).toBe(false)
  })
})

describe('FirstRunWizard 桌面态四步流（NP-06）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setWindowKeys({ tuzhanPet: petBridge })
  })
  afterEach(() => {
    delWindowKeys(['tuzhanPet'])
  })

  async function openWizard(personaName = '菟菚') {
    const wrapper = mount(FirstRunWizard, { props: { show: false, personaName } })
    await wrapper.setProps({ show: true })
    await flushPromises()
    return wrapper
  }

  it('欢迎→Key：默认地址/模型来自配置回显；保存仅 POST 白名单三字段', async () => {
    const wrapper = await openWizard()
    expect(wrapper.text()).toContain('第一次见面')

    await wrapper.get('.frw-primary').trigger('click') // 开始设置
    expect(
      (wrapper.get('input[aria-label="API 地址"]').element as HTMLInputElement).value,
    ).toBe('https://api.deepseek.com/v1')
    expect(
      (wrapper.get('input[aria-label="模型名"]').element as HTMLInputElement).value,
    ).toBe('deepseek-chat')

    await wrapper.get('input[aria-label="API Key"]').setValue('sk-test-123')
    await wrapper.get('.frw-primary').trigger('click') // 保存并继续
    await flushPromises()

    const postCall = apiFetchMock.mock.calls.find((c) => c[1]?.method === 'POST')
    expect(postCall).toBeTruthy()
    const body = JSON.parse(String(postCall![1]!.body)) as Record<string, string>
    expect(body).toEqual({
      llm_base_url: 'https://api.deepseek.com/v1',
      llm_model: 'deepseek-chat',
      llm_api_key: 'sk-test-123',
    })
  })

  it('宠物步骤开关初始值来自 getPetState 真实回显；完成后落 flag + 预填事件 + 关闭', async () => {
    const wrapper = await openWizard()
    await wrapper.get('.frw-primary').trigger('click') // → Key
    await wrapper.get('input[aria-label="API Key"]').setValue('sk-test-123')
    await wrapper.get('.frw-primary').trigger('click') // 保存 → 宠物步骤
    await flushPromises()

    expect(petBridge.getPetState).toHaveBeenCalled()
    expect(
      (wrapper.get('input[aria-label="开启桌面宠物"]').element as HTMLInputElement).checked,
    ).toBe(true)

    await wrapper.get('.frw-primary').trigger('click') // 下一步 → 完成
    expect(wrapper.text()).toContain('都准备好了')

    const prefillSpy = vi.fn()
    window.addEventListener('tztuzhan:prefill-input', prefillSpy)
    await wrapper.get('.frw-primary').trigger('click') // 完成
    window.removeEventListener('tztuzhan:prefill-input', prefillSpy)

    expect(localStorage.getItem(FIRST_RUN_FLAG)).toBeTruthy()
    expect(prefillSpy).toHaveBeenCalledTimes(1)
    expect((prefillSpy.mock.calls[0][0] as CustomEvent).detail).toBe('新手教程')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('空 Key 保存：可见错误提示，不发送 POST', async () => {
    const wrapper = await openWizard()
    await wrapper.get('.frw-primary').trigger('click') // → Key
    await wrapper.get('.frw-primary').trigger('click') // 直接点保存
    await flushPromises()

    expect(wrapper.text()).toContain('请先填入 API Key')
    expect(apiFetchMock.mock.calls.some((c) => c[1]?.method === 'POST')).toBe(false)
    // 停留在 Key 步骤
    expect(wrapper.text()).toContain('先给我一颗「大脑」')
  })

  it('跳过：落 flag、发出关闭、不派预填事件', async () => {
    const wrapper = await openWizard()
    const prefillSpy = vi.fn()
    window.addEventListener('tztuzhan:prefill-input', prefillSpy)
    await wrapper.get('.frw-skip').trigger('click')
    window.removeEventListener('tztuzhan:prefill-input', prefillSpy)

    expect(localStorage.getItem(FIRST_RUN_FLAG)).toBeTruthy()
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(prefillSpy).not.toHaveBeenCalled()
  })
})

describe('FirstRunWizard 网页形态三步流（NP-06）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    delWindowKeys(['tuzhanPet'])
  })

  it('无桥环境不出现宠物步骤：Key 保存后直达完成', async () => {
    const wrapper = mount(FirstRunWizard, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()

    await wrapper.get('.frw-primary').trigger('click') // → Key
    await wrapper.get('input[aria-label="API Key"]').setValue('sk-web-1')
    await wrapper.get('.frw-primary').trigger('click') // 保存 → 直达完成
    await flushPromises()

    expect(wrapper.text()).toContain('都准备好了')
    expect(wrapper.text()).not.toContain('桌面宠物是一扇常驻桌角的小窗')
  })
})
