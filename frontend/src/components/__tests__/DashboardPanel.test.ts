import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getDashboard, type DashboardSummary } from '../../api/dashboard'
import DashboardPanel from '../DashboardPanel.vue'

vi.mock('../../api/dashboard', () => ({ getDashboard: vi.fn() }))

const mockedGetDashboard = vi.mocked(getDashboard)

function fixture(days: number): DashboardSummary {
  return {
    days,
    current: {
      affection: { value: 52, stage: '亲密', bond: '', next: '恋人', next_at: 75, fill: 52 },
      mood: { value: 71, label: '开心' },
      energy: 68,
      resting: false,
      pending_promises: 1,
    },
    timeline: Array.from({ length: days }, (_, index) => ({
      date: `2026-09-${String(index + 1).padStart(2, '0')}`,
      affection: 45 + index,
      mood: 60 + index,
      messages: index,
      user_messages: Math.ceil(index / 2),
      tokens: index * 10,
      calls: index ? 1 : 0,
    })),
    stats: {
      active_days: days - 1,
      messages: 21,
      user_messages: 11,
      tokens: 210,
      cost: 0.0012,
      diaries: 2,
      unlocks: 3,
      unlock_total: 9,
    },
    promises: [{ id: 1, content: '明天继续整理书架', follow_up: '2026-09-05', created_at: '2026-09-04' }],
    recent_affection: [{ value: 52, delta: 2, reason: '认真陪伴', ts: '2026-09-04T12:00:00' }],
  }
}

describe('DashboardPanel', () => {
  beforeEach(() => {
    mockedGetDashboard.mockReset()
    mockedGetDashboard.mockImplementation(async days => fixture(days ?? 30))
  })

  it('loads the default range and renders trends, heatmap and summaries', async () => {
    const wrapper = mount(DashboardPanel, { props: { show: false } })
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(mockedGetDashboard).toHaveBeenCalledWith(30)
    expect(wrapper.text()).toContain('成长总览')
    expect(wrapper.text()).toContain('认真陪伴')
    expect(wrapper.findAll('.heat-cell')).toHaveLength(30)
    expect(wrapper.find('.affection-line').attributes('points')).not.toBe('')
    expect(wrapper.find('.mood-line').attributes('points')).not.toBe('')
  })

  it('reloads when switching to seven days', async () => {
    const wrapper = mount(DashboardPanel, { props: { show: true } })
    await flushPromises()
    await wrapper.findAll('.ranges button')[0].trigger('click')
    await flushPromises()

    expect(mockedGetDashboard).toHaveBeenLastCalledWith(7)
    expect(wrapper.findAll('.heat-cell')).toHaveLength(7)
  })
})
