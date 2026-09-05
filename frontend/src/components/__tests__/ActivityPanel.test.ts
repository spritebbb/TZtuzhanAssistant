import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  completeReading,
  listReadingActivities,
  resumeReading,
  saveReadingNote,
  saveReadingViewpoint,
  setReadingPosition,
  startReading,
  type ReadingActivity,
} from '../../api/activities'
import { listKnowledgeDocuments } from '../../api/knowledge'
import { listGoals, startGoal } from '../../api/goals'
import ActivityPanel from '../ActivityPanel.vue'

vi.mock('../../api/activities', () => ({
  completeReading: vi.fn(),
  listReadingActivities: vi.fn(),
  resumeReading: vi.fn(),
  saveReadingNote: vi.fn(),
  saveReadingViewpoint: vi.fn(),
  setReadingPosition: vi.fn(),
  startReading: vi.fn(),
}))
vi.mock('../../api/knowledge', () => ({ listKnowledgeDocuments: vi.fn() }))
vi.mock('../../api/goals', () => ({
  addGoalProgress: vi.fn(),
  cancelGoal: vi.fn(),
  completeGoal: vi.fn(),
  exportGoalUrl: vi.fn((id: number) => `/api/goals/${id}/export?format=md`),
  listGoals: vi.fn(),
  pauseGoal: vi.fn(),
  resumeGoal: vi.fn(),
  startGoal: vi.fn(),
}))

const mockedList = vi.mocked(listReadingActivities)
const mockedDocuments = vi.mocked(listKnowledgeDocuments)
const mockedStart = vi.mocked(startReading)
const mockedResume = vi.mocked(resumeReading)
const mockedPosition = vi.mocked(setReadingPosition)
const mockedNote = vi.mocked(saveReadingNote)
const mockedViewpoint = vi.mocked(saveReadingViewpoint)
const mockedComplete = vi.mocked(completeReading)
const mockedGoals = vi.mocked(listGoals)
const mockedStartGoal = vi.mocked(startGoal)

function activity(overrides: Partial<ReadingActivity> = {}): ReadingActivity {
  return {
    id: 8,
    kind: 'reading',
    document_id: 3,
    title: '共读《藤本植物.txt》',
    status: 'active',
    position: 0,
    created_at: '2026-09-04T12:00:00',
    updated_at: '2026-09-04T12:00:00',
    completed_at: null,
    filename: '藤本植物.txt',
    format: 'txt',
    chunk_count: 2,
    total: 2,
    progress: 50,
    excerpt: '第一段讲菟丝子的生长。',
    note: '',
    note_count: 0,
    viewpoints: [],
    summary: '',
    ...overrides,
  }
}

describe('ActivityPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedList.mockResolvedValue([])
    mockedGoals.mockResolvedValue([])
    mockedDocuments.mockResolvedValue([{
      id: 3,
      filename: '藤本植物.txt',
      format: 'txt',
      size_bytes: 120,
      chunk_count: 2,
      ts: '2026-09-04T12:00:00',
    }])
    mockedStart.mockResolvedValue(activity())
    mockedResume.mockResolvedValue(activity())
    mockedPosition.mockResolvedValue(activity({
      position: 1,
      progress: 100,
      excerpt: '第二段讲它如何寻找宿主。',
    }))
    mockedNote.mockImplementation(async (_id, content) => activity({ note: content, note_count: content ? 1 : 0 }))
    mockedViewpoint.mockImplementation(async (_id, role, content) => activity({
      viewpoints: [{ role, position: 0, content, ts: '2026-09-04T12:00:00' }],
    }))
    mockedComplete.mockResolvedValue(activity({ status: 'completed', completed_at: '2026-09-04T13:00:00', summary: '《藤本植物.txt》读完时留下的东西：' }))
    mockedStartGoal.mockResolvedValue({
      id: 18,
      kind: 'goal',
      title: '整理作品集',
      status: 'active',
      motivation: '想把做过的事讲清楚',
      next_step: '先挑三个项目',
      support_mode: 'companion',
      reminder_at: null,
      created_at: '2026-09-05T12:00:00',
      updated_at: '2026-09-05T12:00:00',
      completed_at: null,
      progress_entries: [],
      review: '',
    })
  })

  it('starts a reading activity from a bookshelf document', async () => {
    const wrapper = mount(ActivityPanel, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()

    expect(wrapper.text()).toMatch(/从(?:她|菟菚)的书架选一份/)
    await wrapper.get('.document-grid button').trigger('click')
    await flushPromises()

    expect(mockedStart).toHaveBeenCalledWith(3)
    expect(wrapper.text()).toContain('第一段讲菟丝子的生长')
    expect(wrapper.text()).toContain('第 1 / 2 段')
  })

  it('creates a shared goal with an explicit next small step', async () => {
    const wrapper = mount(ActivityPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.get('.goal-section .open-bookshelf').trigger('click')
    const inputs = wrapper.findAll('.goal-create input[type="text"], .goal-create input:not([type])')
    await inputs[0].setValue('整理作品集')
    await wrapper.get('.goal-create textarea').setValue('想把做过的事讲清楚')
    await inputs[1].setValue('先挑三个项目')
    await wrapper.get('.goal-create .talk').trigger('click')
    await flushPromises()

    expect(mockedStartGoal).toHaveBeenCalledWith(expect.objectContaining({
      title: '整理作品集',
      next_step: '先挑三个项目',
      support_mode: 'companion',
    }))
    expect(wrapper.text()).toContain('NEXT SMALL STEP')
  })

  it('saves a bookmark, turns the page and hands a draft back to chat', async () => {
    mockedList.mockResolvedValue([activity()])
    const wrapper = mount(ActivityPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.get('.note-box textarea').setValue('这里像是一种主动的寻找')
    await wrapper.get('.notice-line .secondary').trigger('click')
    await flushPromises()
    expect(mockedNote).toHaveBeenCalledWith(8, '这里像是一种主动的寻找')

    const pageButtons = wrapper.findAll('.page-actions button')
    await pageButtons[1].trigger('click')
    await flushPromises()
    expect(mockedPosition).toHaveBeenCalledWith(8, 1)
    expect(wrapper.text()).toContain('第二段')

    await wrapper.get('.talk').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('discuss')?.[0]?.[0]).toContain('我们继续共读《藤本植物.txt》')
  })

  it('saves role-labeled viewpoints per segment', async () => {
    mockedList.mockResolvedValue([activity()])
    const wrapper = mount(ActivityPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    const boxes = wrapper.findAll('.viewpoints textarea')
    expect(boxes.length).toBe(3)
    await boxes[0].setValue('我觉得菟丝子是在装弱')
    await wrapper.get('.viewpoints .secondary').trigger('click')
    await flushPromises()

    expect(mockedViewpoint).toHaveBeenCalledWith(8, 'user', '我觉得菟丝子是在装弱')
  })

  it('shows the shared book summary from the finished list', async () => {
    mockedList.mockResolvedValue([activity({
      status: 'completed',
      completed_at: '2026-09-04T13:00:00',
      summary: '《藤本植物.txt》读完时留下的东西：\n- 第 1 段的书签：这里像是一种主动的寻找',
    })])
    const wrapper = mount(ActivityPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('有书摘')
    await wrapper.get('.history-row').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('共同书摘')
    expect(wrapper.text()).toContain('这里像是一种主动的寻找')
  })
})
