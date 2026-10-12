import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  cancelReading,
  completeReading,
  finishSegment,
  getBookmarkDraft,
  getReadingMap,
  exportReadingUrl,
  listReadingActivities,
  pauseReading,
  proposeReadingQuestion,
  resumeReading,
  saveReadingNote,
  saveReadingViewpoint,
  setReadingPosition,
  startReading,
  type ReadingActivity,
} from '../../api/activities'
import { listKnowledgeDocuments } from '../../api/knowledge'
import {
  addWritingTurn,
  listWritings,
  requestTuzhanTurn,
  startWriting,
  type CoWriting,
} from '../../api/writings'
import { addObservationEntry, listObservations, startObservation } from '../../api/observations'
import ActivityPanel from '../ActivityPanel.vue'

vi.mock('../../api/activities', () => ({
  cancelReading: vi.fn(),
  completeReading: vi.fn(),
  exportReadingUrl: vi.fn((id: number) => `/api/activities/${id}/export?format=md`),
  finishSegment: vi.fn(),
  getBookmarkDraft: vi.fn(),
  getReadingMap: vi.fn(),
  saveBookmark: vi.fn(),
  listReadingActivities: vi.fn(),
  pauseReading: vi.fn(),
  proposeReadingQuestion: vi.fn(),
  resumeReading: vi.fn(),
  saveReadingNote: vi.fn(),
  saveReadingViewpoint: vi.fn(),
  setReadingPosition: vi.fn(),
  startReading: vi.fn(),
}))
vi.mock('../../api/knowledge', () => ({ listKnowledgeDocuments: vi.fn() }))
vi.mock('../../api/observations', () => ({
  listObservations: vi.fn(async () => []),
  startObservation: vi.fn(),
  getObservation: vi.fn(),
  addObservationEntry: vi.fn(),
  completeObservation: vi.fn(),
  cancelObservation: vi.fn(),
}))
vi.mock('../../api/writings', () => ({
  addWritingTurn: vi.fn(),
  cancelWriting: vi.fn(),
  completeWriting: vi.fn(),
  exportWritingUrl: vi.fn((id: number) => `/api/writings/${id}/export?format=md`),
  listWritings: vi.fn(),
  pauseWriting: vi.fn(),
  requestTuzhanTurn: vi.fn(),
  resumeWriting: vi.fn(),
  startWriting: vi.fn(),
}))

const mockedList = vi.mocked(listReadingActivities)
const mockedDocuments = vi.mocked(listKnowledgeDocuments)
const mockedStart = vi.mocked(startReading)
const mockedPause = vi.mocked(pauseReading)
const mockedCancel = vi.mocked(cancelReading)
const mockedQuestion = vi.mocked(proposeReadingQuestion)
const mockedResume = vi.mocked(resumeReading)
const mockedPosition = vi.mocked(setReadingPosition)
const mockedNote = vi.mocked(saveReadingNote)
const mockedViewpoint = vi.mocked(saveReadingViewpoint)
const mockedComplete = vi.mocked(completeReading)
const mockedWritings = vi.mocked(listWritings)
const mockedStartWriting = vi.mocked(startWriting)
const mockedAddTurn = vi.mocked(addWritingTurn)
const mockedTuzhanTurn = vi.mocked(requestTuzhanTurn)

function writing(overrides: Partial<CoWriting> = {}): CoWriting {
  return {
    id: 22,
    kind: 'writing',
    title: '灯塔看守人的猫',
    status: 'active',
    premise: '一座只会亮一次的灯塔',
    created_at: '2026-09-06T12:00:00',
    updated_at: '2026-09-06T12:00:00',
    completed_at: null,
    turns: [],
    story: '',
    ...overrides,
  }
}

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
    mockedWritings.mockResolvedValue([])
    mockedDocuments.mockResolvedValue([{
      id: 3,
      filename: '藤本植物.txt',
      format: 'txt',
      size_bytes: 120,
      chunk_count: 2,
      ts: '2026-09-04T12:00:00',
    }])
    mockedStart.mockResolvedValue(activity())
    vi.mocked(listObservations).mockResolvedValue([])
    vi.mocked(startObservation).mockResolvedValue({
      id: 91, title: '楼下那棵树', status: 'active',
      created_at: '2026-09-08T10:00:00', updated_at: '2026-09-08T10:00:00', entries: [],
    })
    vi.mocked(addObservationEntry).mockResolvedValue({
      id: 91, title: '楼下那棵树', status: 'active',
      created_at: '2026-09-08T10:00:00', updated_at: '2026-09-08T10:00:00',
      entries: [{
        id: 1, observed_at: '2026-09-08T10:00:00', observer: 'user',
        content: '叶子开始黄了', source_type: 'event', source_id: 3, confidence: 1,
      }],
    })
    vi.mocked(getReadingMap).mockResolvedValue({
      activity_id: 8,
      total: 2,
      read_count: 0,
      confirmed_bookmarks: 0,
      segments: [
        { id: 71, segment_index: 0, title: '第 1 段', status: 'current', source_hash: 'h1', bookmark: null },
        { id: 72, segment_index: 1, title: '第 2 段', status: 'locked', source_hash: 'h2', bookmark: null },
      ],
    })
    vi.mocked(finishSegment).mockResolvedValue({
      activity_id: 8,
      total: 2,
      read_count: 1,
      confirmed_bookmarks: 0,
      segments: [
        { id: 71, segment_index: 0, title: '第 1 段', status: 'read', source_hash: 'h1', bookmark: null },
        { id: 72, segment_index: 1, title: '第 2 段', status: 'current', source_hash: 'h2', bookmark: null },
      ],
    })
    mockedPause.mockResolvedValue(activity({ status: 'paused' }))
    mockedCancel.mockResolvedValue(activity({ status: 'cancelled', completed_at: '2026-09-04T12:30:00' }))
    mockedQuestion.mockResolvedValue('它主动寻找宿主时，你觉得这更像依赖还是生存策略？')
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
  })

  it('shows the reading map and finishes the current segment', async () => {
    const wrapper = mount(ActivityPanel, { props: { show: false, personaName: '菟菚' } })
    await wrapper.setProps({ show: true })
    await flushPromises()
    await wrapper.get('.document-grid button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('阅读地图')
    expect(wrapper.text()).toContain('第 2 段')
    await wrapper.get('.reading-map li.current button').trigger('click')
    await flushPromises()

    expect(finishSegment).toHaveBeenCalledWith(8, 0, 'h1')
    expect(wrapper.text()).toContain('读完 1 / 2 段')
  })

  it('starts an observation log and records one entry', async () => {
    const wrapper = mount(ActivityPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    const inputs = wrapper.findAll('.observation-detail, .goal-create input')
    const titleInput = inputs[inputs.length - 1]
    await titleInput.setValue('楼下那棵树')
    await wrapper.get('.goal-create-actions .talk').trigger('click')
    await flushPromises()
    expect(startObservation).toHaveBeenCalledWith('楼下那棵树')

    await wrapper.get('.observation-detail textarea').setValue('叶子开始黄了')
    await wrapper.get('.observation-detail .plain-action').trigger('click')
    await flushPromises()
    expect(addObservationEntry).toHaveBeenCalledWith(91, '叶子开始黄了')
    expect(wrapper.text()).toContain('叶子开始黄了')
    expect(wrapper.text()).toContain('来源 event#3')
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
    expect(mockedQuestion).toHaveBeenCalledWith(8, '')
    expect(wrapper.emitted('discuss')?.[0]?.[0]).toContain('更像依赖还是生存策略')
    expect(wrapper.emitted('discuss')?.[0]?.[0]).toContain('我的想法是：')
  })

  it('pauses and cancels a reading while keeping an export entry', async () => {
    mockedList.mockResolvedValue([activity()])
    const wrapper = mount(ActivityPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    const actionButtons = wrapper.findAll('.primary-actions button')
    await actionButtons.find(button => button.text() === '暂停')!.trigger('click')
    await flushPromises()
    expect(mockedPause).toHaveBeenCalledWith(8)
    expect(wrapper.text()).toContain('已暂停')
    expect(exportReadingUrl).toHaveBeenCalledWith(8)

    await wrapper.findAll('.primary-actions button').find(button => button.text() === '放下这本')!.trigger('click')
    await flushPromises()
    expect(mockedCancel).toHaveBeenCalledWith(8)
    expect(wrapper.text()).toContain('这场共读已经放下')

    await wrapper.get('.back').trigger('click')
    expect(wrapper.text()).toContain('已放下 · 0 张书签')
    await wrapper.get('.history-row').trigger('click')
    expect(wrapper.text()).toContain('这场共读已经放下')
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

  it('starts a story relay and records the user turn', async () => {
    mockedStartWriting.mockResolvedValue(writing())
    mockedAddTurn.mockImplementation(async (_id, content) =>
      writing({ turns: [{ id: 1, author: 'user', content, ts: '2026-09-06T12:01:00' }] }),
    )
    const wrapper = mount(ActivityPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.get('.writing-section .open-bookshelf').trigger('click')
    const titleInput = wrapper.get('.writing-section .goal-create input').element as HTMLInputElement
    await wrapper.get('.writing-section .goal-create input').setValue('灯塔看守人的猫')
    expect(titleInput.value).toBe('灯塔看守人的猫')
    await wrapper.get('.writing-section .goal-create .talk').trigger('click')
    await flushPromises()

    expect(mockedStartWriting).toHaveBeenCalledWith('灯塔看守人的猫', '', 'story')
    expect(wrapper.text()).toContain('故事还没有正文')

    await wrapper.get('.writing-turn-form textarea').setValue('猫在第七天开始学着数浪。')
    await wrapper.get('.writing-actions button:first-child').trigger('click')
    await flushPromises()

    expect(mockedAddTurn).toHaveBeenCalledWith(22, '猫在第七天开始学着数浪。')
    expect(wrapper.text()).toContain('猫在第七天开始学着数浪')
    expect(wrapper.text()).toContain('虚构')
  })

  it('asks her to continue the story and keeps fiction labeled', async () => {
    mockedWritings.mockResolvedValue([
      writing({
        turns: [
          { id: 1, author: 'user', content: '猫在第七天开始学着数浪。', ts: '2026-09-06T12:01:00' },
        ],
      }),
    ])
    mockedTuzhanTurn.mockImplementation(async () =>
      writing({
        turns: [
          { id: 1, author: 'user', content: '猫在第七天开始学着数浪。', ts: '2026-09-06T12:01:00' },
          { id: 2, author: 'tuzhan', content: '第八天，灯塔亮了第二次。', ts: '2026-09-06T12:02:00' },
        ],
      }),
    )
    const wrapper = mount(ActivityPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.get('.activity-list.goal-list button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('猫在第七天开始学着数浪')

    await wrapper.get('.writing-actions button:last-child').trigger('click')
    await flushPromises()

    expect(mockedTuzhanTurn).toHaveBeenCalledWith(22)
    expect(wrapper.text()).toContain('第八天，灯塔亮了第二次')
    expect(wrapper.text()).toContain('她接了一段')
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
