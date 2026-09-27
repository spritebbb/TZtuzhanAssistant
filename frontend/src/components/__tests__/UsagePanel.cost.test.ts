// NP-07 用量账本金额显示回归：小额金额显示「<¥0.01」，不再渲染一串 0.0000。
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { UsageSummary } from '../../api/usage'

const mockSummary = vi.hoisted(() => vi.fn())

vi.mock('../../api/usage', () => ({
  getUsageSummary: (...args: unknown[]) => mockSummary(...args),
}))

import UsagePanel from '../UsagePanel.vue'

function makeSummary(costToday: number, costPeriod: number, costs: number[]): UsageSummary {
  return {
    today: { prompt: 100, completion: 50, calls: 2, estimated: 0, cost: costToday },
    period: { prompt: 300, completion: 150, calls: 6, estimated: 0, cost: costPeriod },
    days: 7,
    by_channel: costs.map((cost, i) => ({
      channel: `ch${i}`,
      prompt: 10,
      completion: 5,
      calls: 1,
      cost,
    })),
    prices: { input_per_mtok: 2, output_per_mtok: 8 },
  }
}

describe('UsagePanel 金额显示（NP-07）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('0 < 金额 < 0.01 显示「<¥0.01」；0 显示「¥0」；正常金额四位小数', async () => {
    mockSummary.mockResolvedValue(
      makeSummary(0.005, 12.3, [0.008, 0, 3.5]),
    )
    const wrapper = mount(UsagePanel, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('<¥0.01') // 今天 0.005
    expect(text).toContain('¥12.3000') // 近 7 天
    expect(text).toContain('<¥0.01') // 渠道 0.008
    expect(text).toContain('¥0') // 渠道 0
    expect(text).toContain('¥3.5000') // 渠道 3.5
    // 不应出现赤裸的 0.0000
    expect(text).not.toContain('¥0.0000')
  })
})
