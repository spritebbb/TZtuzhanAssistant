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
import {
  collectPossibility,
  deletePossibility,
  generatePossibilityDraft,
  probePossibilities,
  type PossibilityArtifact,
} from '../../api/possibilities'
import {
  captureRelationshipVersion,
  compareRelationshipVersions,
  deleteRelationshipVersion,
  listRelationshipVersions,
  type RelationshipVersion,
  type RelationshipVersionComparison,
} from '../../api/relationshipVersions'
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
vi.mock('../../api/possibilities', () => ({
  probePossibilities: vi.fn(),
  generatePossibilityDraft: vi.fn(),
  collectPossibility: vi.fn(),
  deletePossibility: vi.fn(),
}))
vi.mock('../../api/relationshipVersions', () => ({
  listRelationshipVersions: vi.fn(),
  captureRelationshipVersion: vi.fn(),
  compareRelationshipVersions: vi.fn(),
  deleteRelationshipVersion: vi.fn(),
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
const mockedProbePossibilities = vi.mocked(probePossibilities)
const mockedPossGenerate = vi.mocked(generatePossibilityDraft)
const mockedPossCollect = vi.mocked(collectPossibility)
const mockedPossDelete = vi.mocked(deletePossibility)
const mockedVersionsList = vi.mocked(listRelationshipVersions)
const mockedVersionCapture = vi.mocked(captureRelationshipVersion)
const mockedVersionCompare = vi.mocked(compareRelationshipVersions)
const mockedVersionDelete = vi.mocked(deleteRelationshipVersion)

const possibilityItem: PossibilityArtifact = {
  id: 71,
  artifact_type: 'dream_fragment',
  source_type: 'fiction',
  source_id: 1,
  title: '站台之梦',
  content: '梦里那班地铁准点到达。',
  version: 1,
  created_at: '2026-09-06T21:00:00',
  updated_at: '2026-09-06T21:00:00',
}

function makeVersion(id: number, label: string, overrides: Partial<RelationshipVersion> = {}): RelationshipVersion {
  return {
    id,
    label,
    captured_at: '2026-09-07T10:00:00',
    schema_version: 13,
    created_at: '2026-09-07T10:00:00',
    snapshot: {
      format_version: 1,
      state: { affection: 10, mood: 60, mood_label: '平淡', energy: 70, tension: 0, stage: '初识', resting: false },
      season: { code: 'quiet', label: '沉淀期' },
      behavior: { mood_line: '旧语气。', stage_line: '旧分寸。', texture_line: '', initiative: '', rest_line: '', season_line: '' },
      counts: {
        messages: 12, facts_active: 3, long_memory: 40, events_active: 2,
        artifacts_real: 1, artifacts_fiction: 0, activities_active: 1, activities_completed: 0,
        promises_pending: 1, promises_completed: 0, diary: 2, future_letters: 1,
        dual_perspectives: 1, relationship_snapshots: 0,
      },
    },
    ...overrides,
  }
}

function zeroDeltas(): Record<string, number> {
  return {
    'state.affection': 0, 'state.mood': 0, 'state.energy': 0, 'state.tension': 0,
    'counts.messages': 0, 'counts.facts_active': 0, 'counts.long_memory': 0, 'counts.events_active': 0,
    'counts.artifacts_real': 0, 'counts.artifacts_fiction': 0, 'counts.activities_active': 0,
    'counts.activities_completed': 0, 'counts.promises_pending': 0, 'counts.promises_completed': 0,
    'counts.diary': 0, 'counts.future_letters': 0, 'counts.dual_perspectives': 0,
    'counts.relationship_snapshots': 0,
  }
}

function comparisonFixture(before: RelationshipVersion, after: RelationshipVersion): RelationshipVersionComparison {
  return {
    before,
    after,
    numeric_deltas: { ...zeroDeltas(), 'state.affection': 3, 'counts.messages': 0 },
    changes: [
      { key: 'state.stage', before: '初识', after: '亲密' },
      { key: 'behavior.mood_line', before: '旧语气。', after: '新语气。' },
    ],
  }
}

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
    mockedProbePossibilities.mockReset()
    mockedPossGenerate.mockReset()
    mockedPossCollect.mockReset()
    mockedPossDelete.mockReset()
    mockedVersionsList.mockReset()
    mockedVersionCapture.mockReset()
    mockedVersionCompare.mockReset()
    mockedVersionDelete.mockReset()
    mockedLetters.mockResolvedValue(emptyBoard)
    mockedSnapshots.mockResolvedValue(quietSnapshots)
    mockedDuals.mockResolvedValue([])
    mockedDualAnchors.mockResolvedValue({ events: [], diary: [], goals: [], artifacts: [] })
    mockedProbePossibilities.mockResolvedValue(undefined)
    mockedVersionsList.mockResolvedValue([])
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

  it('creates an artifact-anchored dual perspective from the candidate list', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedDualAnchors.mockResolvedValue({
      events: [],
      diary: [],
      goals: [],
      artifacts: [{ id: 44, label: '2026-09-06 《藤本植物》共同书摘' }],
    })
    mockedDualCreate.mockResolvedValue({ ...dualFixture, source_type: 'artifact', source_id: 44 })
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '新建一页')!.trigger('click')
    await flushPromises()
    const form = wrapper.find('form.compose')
    await form.find('input.line').setValue('一起读完的那天')
    await form.find('input[type="radio"][value="artifact"]').setValue(true)
    await form.find('select[aria-label="选择经历"]').setValue('44')
    await form.trigger('submit')
    await flushPromises()

    expect(mockedDualCreate).toHaveBeenCalledWith({
      title: '一起读完的那天',
      source_type: 'artifact',
      source_id: 44,
    })
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

  it('hides the fiction composer when the possibilities API is unavailable, artifacts unaffected', async () => {
    mockedArtifacts.mockResolvedValue([artifact])
    mockedProbePossibilities.mockRejectedValue(new Error('梦境与平行可能性未开启'))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.find('section.possibilities').exists()).toBe(false)
    expect(wrapper.text()).toContain(artifact.title)
    expect(wrapper.text()).not.toContain('角落暂时打不开')
  })

  it('generates a draft without collecting; collects only on the explicit second action and reloads', async () => {
    mockedArtifacts.mockResolvedValue([])
    mockedPossGenerate.mockResolvedValue('她在梦里说：这班车谁都不会迟到。')
    mockedPossCollect.mockResolvedValue(possibilityItem)
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text() === '编一段')!.trigger('click')
    const compose = wrapper.find('section.possibilities .compose')
    await compose.find('input.line').setValue('站台之梦')
    await compose.find('textarea').setValue('梦见我们坐上了一班不存在的地铁')
    await compose.findAll('button').find((b) => b.text() === '生成草稿')!.trigger('click')
    await flushPromises()

    // 草稿阶段绝不触发收藏
    expect(mockedPossGenerate).toHaveBeenCalledWith('dream', '站台之梦', '梦见我们坐上了一班不存在的地铁')
    expect(mockedPossCollect).not.toHaveBeenCalled()
    const draftsBefore = mockedArtifacts.mock.calls.length

    // 用户编辑正文后，显式点击收藏才落库并重新 load
    const editor = wrapper.find('textarea[aria-label="虚构片段正文"]')
    expect((editor.element as HTMLTextAreaElement).value).toContain('谁都不会迟到')
    await editor.setValue('她编辑过的心愿：这班车谁都不会迟到。')
    await wrapper.findAll('button').find((b) => b.text() === '收藏这个虚构片段')!.trigger('click')
    await flushPromises()

    expect(mockedPossCollect).toHaveBeenCalledWith('dream', '站台之梦', '她编辑过的心愿：这班车谁都不会迟到。')
    expect(mockedArtifacts.mock.calls.length).toBeGreaterThan(draftsBefore)
    expect(wrapper.text()).toContain('编一段')
  })

  it('gives fiction cards a type badge and two-step delete, real artifacts none', async () => {
    mockedArtifacts.mockResolvedValue([
      { ...artifact, id: 41, artifact_type: 'dream_fragment', source_type: 'fiction', title: '站台之梦' },
      artifact,
    ])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('梦境收藏')
    expect(wrapper.text()).toContain('站台之梦')
    const cards = wrapper.findAll('article.artifact-card')
    expect(cards.length).toBe(2)
    expect(cards[0].find('button.delete').exists()).toBe(true)
    expect(cards[1].find('button.delete').exists()).toBe(false)

    await cards[0].find('button.delete').trigger('click')
    expect(mockedPossDelete).not.toHaveBeenCalled()
    expect(cards[0].text()).toContain('确认删除')

    await cards[0].find('button.delete').trigger('click')
    expect(mockedPossDelete).toHaveBeenCalledWith(41)
  })

  it('hides the relationship versions section when its API is unavailable, artifacts unaffected', async () => {
    mockedArtifacts.mockResolvedValue([artifact])
    mockedVersionsList.mockRejectedValue(new Error('不同版本的我们未开启'))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.find('section.versions').exists()).toBe(false)
    expect(wrapper.text()).toContain(artifact.title)
    expect(wrapper.text()).not.toContain('角落暂时打不开')
  })

  it('captures a checkpoint through the explicit label form and reloads', async () => {
    mockedArtifacts.mockResolvedValue([])
    const saved = makeVersion(51, '升级前')
    mockedVersionCapture.mockResolvedValue(saved)
    mockedVersionsList.mockResolvedValueOnce([]).mockResolvedValueOnce([saved])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    const input = wrapper.find('section.versions input.line')
    await input.setValue('升级前')
    await wrapper.findAll('button').find((b) => b.text() === '留下这个版本')!.trigger('click')
    await flushPromises()

    expect(mockedVersionCapture).toHaveBeenCalledWith('升级前')
    // 成功后清空输入并重新 load（列表被再次拉取）
    expect(mockedVersionsList.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect((wrapper.find('section.versions input.line').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('升级前')
    expect(wrapper.text()).toContain('真实记录 12 条')
  })

  it('compares two chosen versions with signed deltas, before→after changes and no verdict copy', async () => {
    mockedArtifacts.mockResolvedValue([])
    const before = makeVersion(5, '升级前')
    const after = makeVersion(9, '这个夏天结束时', {
      snapshot: {
        ...makeVersion(9, '').snapshot,
        state: { affection: 13, mood: 60, mood_label: '平淡', energy: 70, tension: 0, stage: '亲密', resting: false },
        behavior: { mood_line: '新语气。', stage_line: '旧分寸。', texture_line: '', initiative: '', rest_line: '', season_line: '' },
      },
    })
    mockedVersionsList.mockResolvedValue([before, after])
    mockedVersionCompare.mockResolvedValue(comparisonFixture(before, after))
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    const picks = wrapper.find('section.versions .compare-picks')
    await picks.find('select[aria-label="较早版本"]').setValue('5')
    await picks.find('select[aria-label="较晚版本"]').setValue('9')
    await wrapper.findAll('button').find((b) => b.text() === '比较这两个版本')!.trigger('click')
    await flushPromises()

    expect(mockedVersionCompare).toHaveBeenCalledWith(5, 9)
    expect(wrapper.text()).toContain('好感：+3')
    expect(wrapper.text()).toContain('这里只列变化，不判断变好或变坏')
    expect(wrapper.text()).toContain('关系阶段：初识 → 亲密')
    expect(wrapper.text()).toContain('语气基调 变了')
  })

  it('blocks comparing a version with itself and deletes versions only after confirmation', async () => {
    mockedArtifacts.mockResolvedValue([])
    const one = makeVersion(5, '升级前')
    const two = makeVersion(9, '这个夏天结束时')
    mockedVersionsList.mockResolvedValue([one, two])
    const wrapper = mount(CornerPanel, { props: { show: true, personaName: '菟菚' } })
    await flushPromises()

    // 同 id 前端拦截：按钮禁用，点击也不会发出 compare 请求
    const picks = wrapper.find('section.versions .compare-picks')
    await picks.find('select[aria-label="较早版本"]').setValue('5')
    await picks.find('select[aria-label="较晚版本"]').setValue('5')
    const compareButton = wrapper.findAll('button').find((b) => b.text() === '比较这两个版本')!
    expect(compareButton.attributes('disabled')).toBeDefined()
    await compareButton.trigger('click')
    await flushPromises()
    expect(mockedVersionCompare).not.toHaveBeenCalled()

    // 两段式删除
    const deleteButton = wrapper.find('section.versions button.delete')
    await deleteButton.trigger('click')
    expect(mockedVersionDelete).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('确认删除')
    await wrapper.find('section.versions button.delete').trigger('click')
    expect(mockedVersionDelete).toHaveBeenCalledWith(5)
  })
})
