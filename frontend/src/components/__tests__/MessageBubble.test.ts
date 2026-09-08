import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import type { Message } from '../../api/sessions'
import MessageBubble from '../MessageBubble.vue'

vi.mock('../../api/activityDrafts', () => ({
  confirmActivityDraft: vi.fn(async () => ({ ok: true, activity_id: 5, kind: 'goal' })),
}))
vi.mock('../../api/memory', () => ({
  updateFactPinned: vi.fn(async () => undefined),
  deleteFact: vi.fn(async () => undefined),
}))

function botMessage(explanation: Message['explanation']): Message {
  return { role: 'bot', content: '我记得你喜欢猫', ts: 1757300000, explanation }
}

function baseExplanation(memories: NonNullable<Message['explanation']>['memories']) {
  return {
    version: 1,
    state: {
      affection: 40, stage: '熟悉', mood: 60, mood_label: '平静', energy: 80,
    },
    behavior: [],
    memories,
    tools: { search: false, media: 'none' as const },
  }
}

describe('MessageBubble 记忆生命周期露出（G01/F07）', () => {
  it('展示保留说明与「你确认过」标记，且不出现评分', async () => {
    const wrapper = mount(MessageBubble, {
      props: {
        message: botMessage(baseExplanation([
          {
            kind: '长期事实',
            text: '用户喜欢猫',
            lifecycle: {
              fact_id: 7,
              version: 'v1',
              pinned: true,
              expires_at: null,
              tier: 'long',
              retention: '长期保留',
              confidence: 0.9,
              verified_at: null,
              user_confirmed: true,
              can_edit: true,
            },
          },
        ])),
        isStreamingLast: false,
        ttsKey: 'k1',
      },
    })
    await wrapper.get('.whybtn').trigger('click')
    const chip = wrapper.get('.why-lifecycle')
    expect(chip.text()).toContain('长期保留')
    expect(chip.text()).toContain('你确认过')
    expect(wrapper.text()).not.toContain('score')
  })

  it('旧快照（无 lifecycle）不渲染保留行，短期待到期显示日期', async () => {
    const legacy = mount(MessageBubble, {
      props: {
        message: botMessage(baseExplanation([{ kind: '长期事实', text: '用户喜欢猫' }])),
        isStreamingLast: false,
        ttsKey: 'k2',
      },
    })
    await legacy.get('.whybtn').trigger('click')
    expect(legacy.find('.why-lifecycle').exists()).toBe(false)

    const dated = mount(MessageBubble, {
      props: {
        message: botMessage(baseExplanation([
          {
            kind: '长期事实',
            text: '用户最近在学吉他',
            lifecycle: {
              fact_id: 8,
              version: 'v2',
              pinned: false,
              expires_at: '2026-10-08T12:00:00',
              tier: 'short',
              retention: '保留到 2026-10-08',
              confidence: 0.7,
              verified_at: null,
              user_confirmed: false,
              can_edit: true,
            },
          },
        ])),
        isStreamingLast: false,
        ttsKey: 'k3',
      },
    })
    await dated.get('.whybtn').trigger('click')
    expect(dated.get('.why-lifecycle').text()).toContain('保留到 2026-10-08')
  })
})

describe('MessageBubble 活动草稿卡（F06）', () => {
  it('确认草稿调用权威接口并给出回执', async () => {
    const message: Message = {
      role: 'bot',
      content: '好，那就一起做',
      ts: 1757300000,
      draft: {
        draft_id: 'tok.abc', kind: 'goal', title: '一起坚持晨跑',
        payload: { next_step: '先定闹钟' }, expires_at: '2026-09-08T15:20:00',
      },
    }
    const wrapper = mount(MessageBubble, {
      props: { message, isStreamingLast: false, ttsKey: 'k9' },
    })
    expect(wrapper.text()).toContain('一起坚持晨跑')
    await wrapper.get('.draft-card button').trigger('click')
    await Promise.resolve()
    await Promise.resolve()
    const { confirmActivityDraft } = await import('../../api/activityDrafts')
    expect(confirmActivityDraft).toHaveBeenCalledWith('tok.abc')
    expect(wrapper.text()).toContain('好，这就开始')
  })
})

describe('MessageBubble 记忆生命周期操作（F07）', () => {
  it('固定切换与删除都带版本号，删除后收起正文', async () => {
    const message = botMessage(baseExplanation([
      {
        kind: '长期事实',
        text: '用户喜欢猫',
        lifecycle: {
          fact_id: 42, version: 'v9', pinned: false, expires_at: null, tier: 'short',
          retention: '尚待确认', confidence: 0.7, verified_at: null,
          user_confirmed: false, can_edit: true,
        },
      },
    ]))
    const wrapper = mount(MessageBubble, {
      props: { message, isStreamingLast: false, ttsKey: 'k7' },
    })
    await wrapper.get('.whybtn').trigger('click')
    await wrapper.get('.lc-btn').trigger('click')
    await flushPromises()
    const memory = await import('../../api/memory')
    expect(memory.updateFactPinned).toHaveBeenCalledWith(42, true)
    await wrapper.findAll('.lc-btn')[1].trigger('click')
    await flushPromises()
    expect(memory.deleteFact).toHaveBeenCalledWith(42, 'v9')
    expect(wrapper.text()).toContain('这条已经删掉了')
    expect(wrapper.text()).not.toContain('用户喜欢猫')
  })
})
