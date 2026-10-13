import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import type { Message } from '../../api/sessions'
import MessageBubble from '../MessageBubble.vue'

vi.mock('../../api/activityDrafts', () => ({
  confirmActivityDraft: vi.fn(async () => ({ ok: true, activity_id: 5, kind: 'goal' })),
}))
vi.mock('../../api/chat', () => ({
  sendReplyFeedback: vi.fn(async () => ({ id: 5, duplicate: false })),
}))

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

describe('MessageBubble 整轮反馈按钮（38项#32）', () => {
  it('完成的 bot 消息显示「不好」按钮：点击调反馈接口一次并进入已反馈态', async () => {
    const wrapper = mount(MessageBubble, {
      props: {
        message: { role: 'bot', content: '这样处理试试', ts: 1757300000 },
        isStreamingLast: false,
        ttsKey: 'fb-1',
      },
    })
    const btn = wrapper.get('.fbbtn')
    expect(btn.attributes('aria-label')).toContain('不好')
    await btn.trigger('click')
    await flushPromises()
    const chat = await import('../../api/chat')
    expect(chat.sendReplyFeedback).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.fbbtn').classes()).toContain('sent')
    // 已反馈态再点不重复调用
    await wrapper.get('.fbbtn').trigger('click')
    await flushPromises()
    expect(chat.sendReplyFeedback).toHaveBeenCalledTimes(1)
  })

  it('临时轮与流式中的消息不出反馈按钮', () => {
    const ephemeral = mount(MessageBubble, {
      props: {
        message: { role: 'bot', content: '不留痕回复', ephemeral: true, ts: 1757300000 },
        isStreamingLast: false,
        ttsKey: 'fb-2',
      },
    })
    expect(ephemeral.find('.fbbtn').exists()).toBe(false)
    const streaming = mount(MessageBubble, {
      props: {
        message: { role: 'bot', content: '正在写', ts: 1757300000 },
        isStreamingLast: true,
        ttsKey: 'fb-3',
      },
    })
    expect(streaming.find('.fbbtn').exists()).toBe(false)
  })
})
