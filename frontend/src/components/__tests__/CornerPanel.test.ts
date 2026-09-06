import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { listArtifacts, type ArtifactItem } from '../../api/artifacts'
import {
  createFutureLetter,
  deleteFutureLetter,
  listFutureLetters,
  openFutureLetter,
  type FutureLettersBoard,
} from '../../api/futureLetters'
import {
  createRelationshipSnapshot,
  deleteRelationshipSnapshot,
  listRelationshipSnapshots,
  type RelationshipSnapshotsBoard,
} from '../../api/relationshipSnapshots'
import {
  createDualPerspective,
  deleteDualPerspective,
  generateTuzhanDraft,
  listDualAnchorCandidates,
  listDualPerspectives,
  saveDualPerspectiveView,
  type DualPerspective,
} from '../../api/dualPerspectives'
import CornerPanel from '../CornerPanel.vue'

vi.mock('../../api/artifacts', () => ({ listArtifacts: vi.fn() }))
vi.mock('../../api/futureLetters', () => ({
  listFutureLetters: vi.fn(),
  createFutureLetter: vi.fn(),
  openFutureLetter: vi.fn(),
  deleteFutureLetter: vi.fn(),
}))
vi.mock('../../api/relationshipSnapshots', () => ({
  listRelationshipSnapshots: vi.fn(),
  createRelationshipSnapshot: vi.fn(),
  deleteRelationshipSnapshot: vi.fn(),
}))
vi.mock('../../api/dualPerspectives', () => ({
  listDualPerspectives: vi.fn(),
  listDualAnchorCandidates: vi.fn(),
  createDualPerspective: vi.fn(),
  saveDualPerspectiveView: vi.fn(),
  generateTuzhanDraft: vi.fn(),
  deleteDualPerspective: vi.fn(),
}))

const mockedArtifacts = vi.mocked(listArtifacts)
const mockedLetters = vi.mocked(listFutureLetters)
const mockedCreate = vi.mocked(createFutureLetter)
const mockedOpen = vi.mocked(openFutureLetter)
const mockedDelete = vi.mocked(deleteFutureLetter)
const mockedSnapshots = vi.mocked(listRelationshipSnapshots)
const mockedCreatePage = vi.mocked(createRelationshipSnapshot)
const mockedDeletePage = vi.mocked(deleteRelationshipSnapshot)
const mockedDuals = vi.mocked(listDualPerspectives)
const mockedDualAnchors = vi.mocked(listDualAnchorCandidates)
const mockedDualCreate = vi.mocked(createDualPerspective)
const mockedDualSave = vi.mocked(saveDualPerspectiveView)
const mockedDualDraft = vi.mocked(generateTuzhanDraft)
const mockedDualDelete = vi.mocked(deleteDualPerspective)

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

const emptyBoard: FutureLettersBoard = { letters: [], goal_options: [], event_types: [] }
const quietSnapshots: RelationshipSnapshotsBoard = {
  start_date: null,
  today: '2026-09-06',
  days_since: null,
  milestones: [],
  snapshots: [],
}

const dualFixture: DualPerspective = {
  id: 31,
  title: '考试那周',
  source_type: 'free',
  source_id: null,
  source_date: '',
  source_label: '',
  user_view: '我以为你嫌我烦。',
  tuzhan_view: '我只是在担心你。',
  tuzhan_view_origin: 'llm',
  created_at: '2026-09-06T12:00:00',
  updated_at: '2026-09-06T12:00:00',
}

const pageFixture: RelationshipSnapshotsBoard['snapshots'][number] = {
  id: 7,
  snapshot_days: 30,
  start_date: '2026-08-08',
  cutoff_date: '2026-09-06',
  generated_at: '2026-09-06T20:00:00',
  created_at: '2026-09-06T20:00:00',
  updated_at: '2026-09-06T20:00:00',
  source_counts: {
    relationship_events: { count: 3, omitted: 1, cap: 30 },
    diary: { count: 2, omitted: 0, cap: 12 },
  },
  rendered_markdown:
    '# 我们的第 30 天\n\n关系从 2026-08-08 开始，到 2026-09-06，我们一起走过了 30 天。\n\n## 这些日子真实发生的事\n- 2026-08-10 完成了一个约定：一起晨跑',
}

function boardWith(...letters: FutureLettersBoard['letters']): FutureLettersBoard {
  return {
    letters,
    goal_options: [{ id: 5, title: '一起整理完相册', status: 'active' }],
    event_types: [{ type: 'goal_completed', label: '一个共同目标完成' }],
  }
}

describe('CornerPanel', () => {
  beforeEach(() => {
    mockedArtifacts.mockReset()
    mockedLetters.mockReset()
    mockedCreate.mockReset()
    mockedOpen.mockReset()
    mockedDelete.mockReset()
    mockedSnapshots.mockReset()
    mockedCreatePage.mockReset()
    mockedDeletePage.mockReset()
    mockedDuals.mockReset()
    mockedDualAnchors.mockReset()
    mockedDualCreate.mockReset()
    mockedDualSave.mockReset()
    mockedDualDraft.mockReset()
    mockedDualDelete.mockReset()
    mockedLetters.mockResolvedValue(emptyBoard)
    mockedSnapshots.mockResolvedValue(quietSnapshots)
    mockedDuals.mockResolvedValue([])
    mockedDualAnchors.mockResolvedValue({ events: [], diary: [], goals: [] })
  })

  it('shows real artifacts with type labels and provenance date', async () => {
    mockedArtifacts.mockResolvedValue([artifact])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('我们的角落')
    expect(wrapper.text()).toContain('共同书摘')
    expect(wrapper.text()).toContain('《藤本植物.txt》共同书摘')
    expect(wrapper.text()).toContain('这里像是一种主动的寻找')
    expect(wrapper.text()).not.toContain('已更新')
  })

  it('explains the empty corner without inventing history', async () => {
    mockedArtifacts.mockResolvedValue([])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('这里还空着')
    expect(wrapper.text()).toContain('读完第一本书')
  })

  it('labels a goal review as a real shared artifact', async () => {
    mockedArtifacts.mockResolvedValue([{ ...artifact, artifact_type: 'goal_review', title: '作品集过程回顾' }])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('目标回顾')
    expect(wrapper.text()).toContain('作品集过程回顾')
  })

  it('marks updated artifacts with a version badge', async () => {
    mockedArtifacts.mockResolvedValue([{ ...artifact, version: 3 }])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('已更新 3 版')
  })

  it('keeps a sealed letter locked: shows only condition, never the body', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedLetters.mockResolvedValue(boardWith({
      id: 11,
      title: '一年后的我们',
      unlock_type: 'date',
      unlock_at: '2027-09-06T20:00:00',
      goal_id: null,
      goal_title: '',
      event_type: null,
      status: 'sealed',
      unlocked_at: null,
      unlocked_by_event_id: null,
      created_at: '2026-09-06T12:00:00',
      opened_at: null,
    }))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('写给未来的我们')
    expect(wrapper.text()).toContain('一年后的我们')
    expect(wrapper.text()).toContain('封存中')
    expect(wrapper.text()).toContain('2027-09-06 20:00')
    const html = wrapper.html()
    expect(html).not.toContain('只有拆开才能看到的悄悄话')
    expect(wrapper.find('.letter-body').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('只有拆开才能看到的悄悄话')
  })

  it('opens a ready letter on explicit action and shows the body', async () => {
    mockedArtifacts.mockResolvedValue([])
    const sealed = boardWith({
      id: 12,
      title: '',
      unlock_type: 'goal',
      unlock_at: null,
      goal_id: 5,
      goal_title: '一起整理完相册',
      event_type: null,
      status: 'ready',
      unlocked_at: '2026-09-06T18:00:00',
      unlocked_by_event_id: null,
      created_at: '2026-09-01T12:00:00',
      opened_at: null,
    })
    mockedOpen.mockResolvedValue({
      ...sealed.letters[0],
      status: 'opened',
      opened_at: '2026-09-06T19:00:00',
      body: '相册整理完啦，这封信是给我们的奖励。',
    })
    mockedLetters.mockResolvedValueOnce(sealed).mockResolvedValueOnce(boardWith({
      ...sealed.letters[0],
      status: 'opened',
      opened_at: '2026-09-06T19:00:00',
      body: '相册整理完啦，这封信是给我们的奖励。',
    }))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('可拆')
    expect(wrapper.text()).toContain('等「一起整理完相册」完成的那天')
    await wrapper.find('button.open').trigger('click')
    await flushPromises()

    expect(mockedOpen).toHaveBeenCalledWith(12)
    expect(wrapper.text()).toContain('相册整理完啦，这封信是给我们的奖励。')
    expect(wrapper.text()).toContain('已拆')
  })

  it('composes a date letter through the form', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedCreate.mockResolvedValue({
      id: 13,
      title: '',
      unlock_type: 'date',
      unlock_at: '2027-01-01T00:00:00',
      goal_id: null,
      goal_title: '',
      event_type: null,
      status: 'sealed',
      unlocked_at: null,
      unlocked_by_event_id: null,
      created_at: '2026-09-06T12:00:00',
      opened_at: null,
    })
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '写一封')!.trigger('click')
    const form = wrapper.find('form.compose')
    await form.find('input.line').setValue('新年第一天')
    await form.find('textarea').setValue('新的一年也要在一起。')
    await form.find('input[type="datetime-local"]').setValue('2027-01-01T00:00')
    await form.trigger('submit')

    expect(mockedCreate).toHaveBeenCalledWith({
      body: '新的一年也要在一起。',
      unlock_type: 'date',
      title: '新年第一天',
      unlock_at: '2027-01-01T00:00',
    })
  })

  it('deletes a letter only after an explicit confirmation click', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedLetters.mockResolvedValue(boardWith({
      id: 14,
      title: '不想留的信',
      unlock_type: 'event',
      unlock_at: null,
      goal_id: null,
      goal_title: '',
      event_type: 'goal_completed',
      status: 'sealed',
      unlocked_at: null,
      unlocked_by_event_id: null,
      created_at: '2026-09-06T12:00:00',
      opened_at: null,
    }))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    const deleteButton = wrapper.find('button.delete')
    await deleteButton.trigger('click')
    expect(mockedDelete).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('确认删除')

    await wrapper.find('button.delete').trigger('click')
    expect(mockedDelete).toHaveBeenCalledWith(14)
  })

  it('stays quiet when no milestone is due and nothing was saved', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedSnapshots.mockResolvedValue({
      start_date: '2026-08-08',
      today: '2026-09-06',
      days_since: 30,
      milestones: [
        { days: 30, eligible: true, has_snapshot: true, snapshot_id: 7 },
        { days: 100, eligible: false, has_snapshot: false, snapshot_id: null },
        { days: 365, eligible: false, has_snapshot: false, snapshot_id: null },
      ],
      snapshots: [pageFixture],
    })
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    // 已有 30 天快照 → 显示那一页；100/365 未到 → 不出现空占位
    expect(wrapper.find('section.pages').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('第 100 天')
    expect(wrapper.text()).not.toContain('第 365 天')
  })

  it('invites creating a due milestone page and calls the API on explicit click', async () => {
    mockedArtifacts.mockResolvedValue([])
    const dueBoard: RelationshipSnapshotsBoard = {
      start_date: '2026-08-08',
      today: '2026-09-06',
      days_since: 30,
      milestones: [
        { days: 30, eligible: true, has_snapshot: false, snapshot_id: null },
        { days: 100, eligible: false, has_snapshot: false, snapshot_id: null },
        { days: 365, eligible: false, has_snapshot: false, snapshot_id: null },
      ],
      snapshots: [],
    }
    const savedBoard: RelationshipSnapshotsBoard = {
      ...dueBoard,
      milestones: [
        { days: 30, eligible: true, has_snapshot: true, snapshot_id: 21 },
        { days: 100, eligible: false, has_snapshot: false, snapshot_id: null },
        { days: 365, eligible: false, has_snapshot: false, snapshot_id: null },
      ],
      snapshots: [pageFixture],
    }
    mockedCreatePage.mockResolvedValue(pageFixture)
    mockedSnapshots.mockResolvedValueOnce(dueBoard).mockResolvedValueOnce(savedBoard)
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('第 30 天')
    expect(wrapper.text()).toContain('整理这一页')
    await wrapper.findAll('button').find((b) => b.text() === '整理这一页')!.trigger('click')
    await flushPromises()

    expect(mockedCreatePage).toHaveBeenCalledWith(30)
    expect(wrapper.text()).toContain('2026-08-08 → 2026-09-06')
    expect(wrapper.text()).toContain('真实发生的事 3（另有 1 条未列入）')
  })

  it('shows a saved snapshot with dates, explicit source counts and escaped markdown text', async () => {
    mockedArtifacts.mockResolvedValue([{ ...artifact, artifact_type: 'relationship_snapshot', title: '我们的第 30 天' }])
    mockedSnapshots.mockResolvedValue({
      start_date: '2026-08-08',
      today: '2026-09-06',
      days_since: 30,
      milestones: [
        { days: 30, eligible: true, has_snapshot: true, snapshot_id: 7 },
        { days: 100, eligible: false, has_snapshot: false, snapshot_id: null },
        { days: 365, eligible: false, has_snapshot: false, snapshot_id: null },
      ],
      snapshots: [pageFixture],
    })
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('我们的纪念页')
    expect(wrapper.text()).toContain('日记 2')
    expect(wrapper.find('pre.page-body').text()).toContain('一起晨跑')
    // 快照 artifact 由纪念页一节承担，不再在「一起做成的事」重复陈列
    expect(wrapper.findAll('article.artifact-card').length).toBe(0)
  })

  it('deletes a page only after an explicit confirmation click', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedSnapshots.mockResolvedValue({
      start_date: '2026-08-08',
      today: '2026-09-06',
      days_since: 30,
      milestones: [
        { days: 30, eligible: true, has_snapshot: true, snapshot_id: 7 },
        { days: 100, eligible: false, has_snapshot: false, snapshot_id: null },
        { days: 365, eligible: false, has_snapshot: false, snapshot_id: null },
      ],
      snapshots: [pageFixture],
    })
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    const deleteButton = wrapper.findAll('button.delete')[0]
    await deleteButton.trigger('click')
    expect(mockedDeletePage).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('确认删除')

    await wrapper.findAll('button.delete')[0].trigger('click')
    expect(mockedDeletePage).toHaveBeenCalledWith(30)
  })

  it('keeps letters and artifacts available when snapshots are disabled', async () => {
    mockedArtifacts.mockResolvedValue([artifact])
    mockedSnapshots.mockRejectedValue(new Error('关系快照未开启'))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.find('section.pages').exists()).toBe(false)
    expect(wrapper.find('section.letters').exists()).toBe(true)
    expect(wrapper.text()).toContain(artifact.title)
    expect(wrapper.text()).not.toContain('角落暂时打不开')
  })

  it('keeps existing artifacts available when future letters are disabled', async () => {
    mockedArtifacts.mockResolvedValue([artifact])
    mockedLetters.mockRejectedValue(new Error('未来信件未开启'))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain(artifact.title)
    expect(wrapper.find('section.letters').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('角落暂时打不开')
  })

  it('creates a free-theme dual perspective through the form', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedDualCreate.mockResolvedValue(dualFixture)
    mockedDuals.mockResolvedValueOnce([]).mockResolvedValueOnce([dualFixture])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '新建一页')!.trigger('click')
    const form = wrapper.find('form.compose')
    await form.find('input.line').setValue('考试那周')
    await form.find('textarea').setValue('我以为你嫌我烦。')
    await form.trigger('submit')
    await flushPromises()

    expect(mockedDualCreate).toHaveBeenCalledWith({
      title: '考试那周',
      source_type: 'free',
      user_view: '我以为你嫌我烦。',
    })
    expect(wrapper.text()).toContain('她记得的')
    expect(wrapper.text()).toContain('我只是在担心你。')
    expect(wrapper.text()).toContain('她想')
  })

  it('fills her side with an LLM draft on demand and saves it as llm-origin', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedDuals.mockResolvedValue([{ ...dualFixture, tuzhan_view: '', tuzhan_view_origin: 'user' }])
    mockedDualDraft.mockResolvedValue('后来才懂那是我的笨拙关心。')
    mockedDualSave.mockResolvedValue({ ...dualFixture, tuzhan_view: '后来才懂那是我的笨拙关心。' })
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '请她想一想')!.trigger('click')
    await flushPromises()
    expect(mockedDualDraft).toHaveBeenCalledWith(31)

    const editor = wrapper.find('textarea[aria-label="她的版本编辑"]')
    expect((editor.element as HTMLTextAreaElement).value).toBe('后来才懂那是我的笨拙关心。')
    await wrapper.findAll('button').find((b) => b.text() === '保存她的版本')!.trigger('click')
    await flushPromises()
    expect(mockedDualSave).toHaveBeenCalledWith(31, 'tuzhan', '后来才懂那是我的笨拙关心。', 'llm')
  })

  it('hides the dual perspectives section when disabled, artifacts unaffected', async () => {
    mockedArtifacts.mockResolvedValue([artifact])
    mockedDuals.mockRejectedValue(new Error('双视角叙事未开启'))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.find('section.duals').exists()).toBe(false)
    expect(wrapper.text()).toContain(artifact.title)
    expect(wrapper.text()).not.toContain('角落暂时打不开')
  })
})
