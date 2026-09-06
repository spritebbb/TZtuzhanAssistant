import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  generateActivityViewpointDraft,
  getActivityViewpoints,
  saveActivityViewpoint,
} from '../../api/activities'
import ViewpointBlock from '../ViewpointBlock.vue'

vi.mock('../../api/activities', () => ({
  getActivityViewpoints: vi.fn(),
  saveActivityViewpoint: vi.fn(),
  generateActivityViewpointDraft: vi.fn(),
}))

const mockedLoad = vi.mocked(getActivityViewpoints)
const mockedSave = vi.mocked(saveActivityViewpoint)
const mockedDraft = vi.mocked(generateActivityViewpointDraft)

function payloadWith(overrides: Record<string, unknown> = {}) {
  return {
    activity_id: 9,
    kind: 'goal',
    title: '坚持晨跑',
    status: 'active',
    viewpoints: [
      { role: 'user' as const, position: -1, content: '有你在后面追着。', ts: '2026-09-06T08:00:00' },
    ],
    ...overrides,
  }
}

describe('ViewpointBlock', () => {
  beforeEach(() => {
    mockedLoad.mockReset()
    mockedSave.mockReset()
    mockedDraft.mockReset()
    mockedLoad.mockResolvedValue(payloadWith())
  })

  it('renders both sides from the server, editing own side inline', async () => {
    const wrapper = mount(ViewpointBlock, { props: { activityId: 9, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('双方感想')
    expect((wrapper.find('textarea[aria-label="你的感想"]').element as HTMLTextAreaElement).value)
      .toBe('有你在后面追着。')
    expect(wrapper.find('.vp-text').text()).toBe('（她还没说）')
  })

  it('fills her side with an on-demand draft into the editor', async () => {
    mockedDraft.mockResolvedValue('我想陪你把这条路一直跑下去。')
    const wrapper = mount(ViewpointBlock, { props: { activityId: 9, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '请她想一想')!.trigger('click')
    await flushPromises()

    expect(mockedDraft).toHaveBeenCalledWith(9)
    const editor = wrapper.find('textarea[aria-label="她的感想编辑"]')
    expect((editor.element as HTMLTextAreaElement).value).toBe('我想陪你把这条路一直跑下去。')
  })

  it('saves each side through the generalized viewpoint API', async () => {
    const savedViewpoints = [
      { role: 'user' as const, position: -1, content: '改后的版本。', ts: '2026-09-06T09:00:00' },
      { role: 'tuzhan' as const, position: -1, content: '我会每天在楼下等你。', ts: '2026-09-06T09:01:00' },
    ]
    mockedSave.mockResolvedValue(savedViewpoints)
    const wrapper = mount(ViewpointBlock, { props: { activityId: 9, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '写她的版本')!.trigger('click')
    await wrapper.find('textarea[aria-label="她的感想编辑"]').setValue('我会每天在楼下等你。')
    await wrapper.findAll('button').find((b) => b.text() === '保存她的感想')!.trigger('click')
    await flushPromises()

    expect(mockedSave).toHaveBeenCalledWith(9, 'tuzhan', '我会每天在楼下等你。')
    expect(wrapper.find('.vp-text').text()).toBe('我会每天在楼下等你。')

    await wrapper.find('textarea[aria-label="你的感想"]').setValue('改后的版本。')
    await wrapper.findAll('button').find((b) => b.text() === '保存你的感想')!.trigger('click')
    await flushPromises()
    expect(mockedSave).toHaveBeenCalledWith(9, 'user', '改后的版本。')
  })

  it('stays hidden when the activity cannot be read', async () => {
    mockedLoad.mockRejectedValue(new Error('活动记录不存在'))
    const wrapper = mount(ViewpointBlock, { props: { activityId: 404, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.find('section.vp-block').exists()).toBe(false)
  })
})
