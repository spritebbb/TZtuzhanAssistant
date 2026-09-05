import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { listArtifacts, type ArtifactItem } from '../../api/artifacts'
import CornerPanel from '../CornerPanel.vue'

vi.mock('../../api/artifacts', () => ({ listArtifacts: vi.fn() }))

const mocked = vi.mocked(listArtifacts)

const artifact: ArtifactItem = {
  id: 1,
  artifact_type: 'book_summary',
  source_type: 'activity',
  source_id: 9,
  title: '《藤本植物.txt》共同书摘',
  content: '《藤本植物.txt》读完时留下的东西：\n- 第 1 段的书签：这里像是一种主动的寻找',
  version: 1,
  created_at: '2026-09-05T12:00:00',
  updated_at: '2026-09-05T12:00:00',
}

describe('CornerPanel', () => {
  beforeEach(() => {
    mocked.mockReset()
  })

  it('shows real artifacts with type labels and provenance date', async () => {
    mocked.mockResolvedValue([artifact])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('我们的角落')
    expect(wrapper.text()).toContain('共同书摘')
    expect(wrapper.text()).toContain('《藤本植物.txt》共同书摘')
    expect(wrapper.text()).toContain('这里像是一种主动的寻找')
    expect(wrapper.text()).not.toContain('已更新')
  })

  it('explains the empty corner without inventing history', async () => {
    mocked.mockResolvedValue([])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('这里还空着')
    expect(wrapper.text()).toContain('读完第一本书')
  })

  it('labels a goal review as a real shared artifact', async () => {
    mocked.mockResolvedValue([{ ...artifact, artifact_type: 'goal_review', title: '作品集过程回顾' }])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('目标回顾')
    expect(wrapper.text()).toContain('作品集过程回顾')
  })

  it('marks updated artifacts with a version badge', async () => {
    mocked.mockResolvedValue([{ ...artifact, version: 3 }])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('已更新 3 版')
  })
})
