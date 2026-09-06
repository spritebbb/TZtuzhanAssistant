<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { listArtifacts, type ArtifactItem } from '../api/artifacts'
import {
  createDualPerspective,
  deleteDualPerspective,
  generateTuzhanDraft,
  listDualAnchorCandidates,
  listDualPerspectives,
  saveDualPerspectiveView,
  type DualAnchorCandidates,
  type DualPerspective,
} from '../api/dualPerspectives'
import {
  createFutureLetter,
  deleteFutureLetter,
  listFutureLetters,
  openFutureLetter,
  type EventTypeOption,
  type FutureLetter,
  type GoalOption,
  type UnlockType,
} from '../api/futureLetters'
import {
  createRelationshipSnapshot,
  deleteRelationshipSnapshot,
  listRelationshipSnapshots,
  type RelationshipSnapshot,
  type SnapshotMilestone,
} from '../api/relationshipSnapshots'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const artifacts = ref<ArtifactItem[]>([])
const letters = ref<FutureLetter[]>([])
const goalOptions = ref<GoalOption[]>([])
const eventTypes = ref<EventTypeOption[]>([])
const lettersAvailable = ref(true)
const snapshots = ref<RelationshipSnapshot[]>([])
const snapshotMilestones = ref<SnapshotMilestone[]>([])
const snapshotsAvailable = ref(true)
const duals = ref<DualPerspective[]>([])
const dualsAvailable = ref(true)
const loading = ref(false)
const error = ref('')

const TYPE_LABELS: Record<string, string> = {
  book_summary: '共同书摘',
  goal_review: '目标回顾',
  co_story: '共同故事',
  future_letter: '未来信件',
  relationship_snapshot: '纪念页',
}

function typeLabel(type: string) {
  return TYPE_LABELS[type] || '共同回忆'
}

// ---- 写信表单 ----
const composing = ref(false)
const busy = ref(false)
const formError = ref('')
const draftTitle = ref('')
const draftBody = ref('')
const draftType = ref<UnlockType>('date')
const draftDate = ref('')
const draftGoalId = ref<number | ''>('')
const draftEventType = ref('')

const hasActiveGoal = computed(() => goalOptions.value.length > 0)

function resetDraft() {
  draftTitle.value = ''
  draftBody.value = ''
  draftType.value = 'date'
  draftDate.value = ''
  draftGoalId.value = ''
  draftEventType.value = ''
  formError.value = ''
}

function statusLabel(status: FutureLetter['status']) {
  if (status === 'ready') return '可拆'
  if (status === 'opened') return '已拆'
  return '封存中'
}

function eventLabel(type: string | null) {
  if (!type) return '一件真实的事'
  const found = eventTypes.value.find((item) => item.type === type)
  return found ? found.label : '一件真实的事'
}

function conditionText(letter: FutureLetter) {
  if (letter.unlock_type === 'date' && letter.unlock_at) {
    return `到 ${letter.unlock_at.slice(0, 16).replace('T', ' ')} 就能拆`
  }
  if (letter.unlock_type === 'goal') {
    return letter.goal_title ? `等「${letter.goal_title}」完成的那天` : '等一个共同目标完成的那天'
  }
  return `等「${eventLabel(letter.event_type)}」发生之后`
}

function toggleCompose() {
  composing.value = !composing.value
  if (!composing.value) resetDraft()
}

async function load() {
  loading.value = true
  error.value = ''
  const [artifactResult, lettersResult, snapshotsResult, dualsResult] = await Promise.allSettled([
    listArtifacts(),
    listFutureLetters(),
    listRelationshipSnapshots(),
    listDualPerspectives(),
  ])
  if (artifactResult.status === 'fulfilled') {
    artifacts.value = artifactResult.value
  } else {
    error.value = '角落暂时打不开，过会儿再来'
  }
  if (lettersResult.status === 'fulfilled') {
    const board = lettersResult.value
    lettersAvailable.value = true
    letters.value = board.letters
    goalOptions.value = board.goal_options
    eventTypes.value = board.event_types
  } else {
    // 后端关闭 feature flag 或尚未升级时，只隐藏未来信件，不影响原有共同产物。
    lettersAvailable.value = false
    letters.value = []
    goalOptions.value = []
    eventTypes.value = []
  }
  if (snapshotsResult.status === 'fulfilled') {
    snapshotsAvailable.value = true
    snapshots.value = snapshotsResult.value.snapshots
    snapshotMilestones.value = snapshotsResult.value.milestones
  } else {
    // 同理：纪念页独立降级，不牵连未来信件与共同产物。
    snapshotsAvailable.value = false
    snapshots.value = []
    snapshotMilestones.value = []
  }
  if (dualsResult.status === 'fulfilled') {
    dualsAvailable.value = true
    duals.value = dualsResult.value
  } else {
    // 双视角同样独立降级。
    dualsAvailable.value = false
    duals.value = []
  }
  loading.value = false
}

async function submitLetter() {
  if (busy.value) return
  formError.value = ''
  const body = draftBody.value.trim()
  if (!body) {
    formError.value = '信总要写点什么吧'
    return
  }
  const payload: Parameters<typeof createFutureLetter>[0] = {
    body,
    unlock_type: draftType.value,
    title: draftTitle.value.trim(),
  }
  if (draftType.value === 'date') {
    if (!draftDate.value) {
      formError.value = '选一个拆信的时刻'
      return
    }
    payload.unlock_at = draftDate.value
  } else if (draftType.value === 'goal') {
    if (!draftGoalId.value) {
      formError.value = '选一个共同目标'
      return
    }
    payload.goal_id = draftGoalId.value
  } else {
    if (!draftEventType.value) {
      formError.value = '选一件等它发生的事'
      return
    }
    payload.event_type = draftEventType.value
  }
  busy.value = true
  try {
    await createFutureLetter(payload)
    composing.value = false
    resetDraft()
    await load()
  } catch (exc) {
    formError.value = exc instanceof Error ? exc.message : '信没有寄出去，再试一次'
  } finally {
    busy.value = false
  }
}

async function openLetter(letter: FutureLetter) {
  try {
    await openFutureLetter(letter.id)
    await load()
  } catch {
    error.value = '拆信没有成功，过会儿再试'
  }
}

const confirmDeleteId = ref<number | null>(null)

async function removeLetter(letter: FutureLetter) {
  if (confirmDeleteId.value !== letter.id) {
    confirmDeleteId.value = letter.id
    return
  }
  confirmDeleteId.value = null
  try {
    await deleteFutureLetter(letter.id)
    await load()
  } catch {
    error.value = '删除没有成功，过会儿再试'
  }
}

// ---- 我们的纪念页（关系快照）----
const SOURCE_LABELS: Record<string, string> = {
  relationship_events: '真实发生的事',
  artifacts: '共同产物',
  diary: '日记',
  user_terms: '口头禅',
  activity_viewpoints: '各自的想法',
}
const snapshotBusy = ref(false)
const snapshotError = ref('')
const confirmDeleteDays = ref<number | null>(null)

// 没到日子的里程碑不展示空占位；有可整理的或已有的才出现这一节。
const savedPages = computed(() => [...snapshots.value].sort((a, b) => a.snapshot_days - b.snapshot_days))
const creatablePages = computed(() =>
  snapshotMilestones.value.filter((item) => item.eligible && !item.has_snapshot),
)
const showPages = computed(
  () => snapshotsAvailable.value && (savedPages.value.length > 0 || creatablePages.value.length > 0),
)
// 快照内容以纪念页一节为准；「一起做成的事」里不再重复陈列。
const visibleArtifacts = computed(() =>
  artifacts.value.filter((item) => item.artifact_type !== 'relationship_snapshot'),
)

function sourceCountText(page: RelationshipSnapshot) {
  const entries = Object.entries(page.source_counts || {})
  if (!entries.length) return ''
  return entries
    .map(([key, meta]) => {
      const label = SOURCE_LABELS[key] || key
      const omitted = meta && meta.omitted > 0 ? `（另有 ${meta.omitted} 条未列入）` : ''
      return `${label} ${meta ? meta.count : 0}${omitted}`
    })
    .join(' · ')
}

async function createPage(days: number) {
  if (snapshotBusy.value) return
  snapshotBusy.value = true
  snapshotError.value = ''
  try {
    await createRelationshipSnapshot(days)
    await load()
  } catch (exc) {
    snapshotError.value = exc instanceof Error ? exc.message : '这一页没有整理好，再试一次'
  } finally {
    snapshotBusy.value = false
  }
}

async function removeSnapshot(page: RelationshipSnapshot) {
  if (confirmDeleteDays.value !== page.snapshot_days) {
    confirmDeleteDays.value = page.snapshot_days
    return
  }
  confirmDeleteDays.value = null
  try {
    await deleteRelationshipSnapshot(page.snapshot_days)
    await load()
  } catch {
    snapshotError.value = '删除没有成功，过会儿再试'
  }
}

// ---- 双视角叙事 ----
const dualComposing = ref(false)
const dualBusy = ref(false)
const dualError = ref('')
const dualTitle = ref('')
const dualAnchorType = ref<'free' | 'event' | 'diary' | 'goal'>('free')
const dualAnchorId = ref<number | ''>('')
const dualUserView = ref('')
const anchorCandidates = ref<DualAnchorCandidates | null>(null)
const dualSaveBusyId = ref<number | null>(null)
const dualDraftBusyId = ref<number | null>(null)
const dualDraftTargetId = ref<number | null>(null)
const confirmDeleteDualId = ref<number | null>(null)
const dualTuzhanDrafts = ref<Record<number, string>>({})
const dualDraftOrigin = ref<Record<number, 'llm' | 'user'>>({})

function toggleTuzhanEditor(page: DualPerspective) {
  if (dualDraftTargetId.value === page.id) {
    dualDraftTargetId.value = null
    return
  }
  dualDraftTargetId.value = page.id
  if (dualTuzhanDrafts.value[page.id] === undefined) {
    dualTuzhanDrafts.value[page.id] = page.tuzhan_view
  }
  dualDraftOrigin.value[page.id] = 'user'
}

function originLabel(page: DualPerspective) {
  return page.tuzhan_view_origin === 'llm' ? '她想' : '代填'
}

function toggleDualCompose() {
  dualComposing.value = !dualComposing.value
  if (!dualComposing.value) {
    dualTitle.value = ''
    dualAnchorType.value = 'free'
    dualAnchorId.value = ''
    dualUserView.value = ''
    dualError.value = ''
    return
  }
  if (!anchorCandidates.value) {
    listDualAnchorCandidates()
      .then((candidates) => { anchorCandidates.value = candidates })
      .catch(() => { anchorCandidates.value = { events: [], diary: [], goals: [] } })
  }
}

function anchorOptions() {
  if (!anchorCandidates.value) return []
  if (dualAnchorType.value === 'event') return anchorCandidates.value.events
  if (dualAnchorType.value === 'diary') return anchorCandidates.value.diary
  if (dualAnchorType.value === 'goal') return anchorCandidates.value.goals
  return []
}

async function submitDual() {
  if (dualBusy.value) return
  dualError.value = ''
  const title = dualTitle.value.trim()
  if (!title) {
    dualError.value = '给这段经历起个名字'
    return
  }
  const payload: Parameters<typeof createDualPerspective>[0] = { title, source_type: dualAnchorType.value }
  if (dualAnchorType.value !== 'free') {
    if (!dualAnchorId.value) {
      dualError.value = '选一段真实经历，或改回自由主题'
      return
    }
    payload.source_id = dualAnchorId.value
  }
  if (dualUserView.value.trim()) payload.user_view = dualUserView.value.trim()
  dualBusy.value = true
  try {
    await createDualPerspective(payload)
    dualComposing.value = false
    dualTitle.value = ''
    dualAnchorType.value = 'free'
    dualAnchorId.value = ''
    dualUserView.value = ''
    await load()
  } catch (exc) {
    dualError.value = exc instanceof Error ? exc.message : '这一页没有建成，再试一次'
  } finally {
    dualBusy.value = false
  }
}

async function saveTuzhanView(page: DualPerspective, origin: 'llm' | 'user') {
  if (dualSaveBusyId.value !== null) return
  const content = (dualTuzhanDrafts.value[page.id] ?? page.tuzhan_view).trim()
  if (!content && origin !== 'llm') {
    dualError.value = '要写点什么才能保存'
    return
  }
  dualSaveBusyId.value = page.id
  dualError.value = ''
  try {
    await saveDualPerspectiveView(page.id, 'tuzhan', content, origin)
    dualDraftTargetId.value = null
    dualTuzhanDrafts.value[page.id] = ''
    await load()
  } catch (exc) {
    dualError.value = exc instanceof Error ? exc.message : '保存没有成功'
  } finally {
    dualSaveBusyId.value = null
  }
}

async function draftTuzhanView(page: DualPerspective) {
  if (dualDraftBusyId.value !== null) return
  dualDraftBusyId.value = page.id
  dualError.value = ''
  dualDraftTargetId.value = page.id
  try {
    dualTuzhanDrafts.value[page.id] = await generateTuzhanDraft(page.id)
    dualDraftOrigin.value[page.id] = 'llm'
  } catch (exc) {
    dualError.value = exc instanceof Error ? exc.message : '草稿没有生成'
  } finally {
    dualDraftBusyId.value = null
  }
}

async function removeDual(page: DualPerspective) {
  if (confirmDeleteDualId.value !== page.id) {
    confirmDeleteDualId.value = page.id
    return
  }
  confirmDeleteDualId.value = null
  try {
    await deleteDualPerspective(page.id)
    await load()
  } catch {
    dualError.value = '删除没有成功，过会儿再试'
  }
}

watch(() => props.show, (show) => { if (show) void load() }, { immediate: true })
</script>

<template>
  <div v-if="show" class="corner-mask" @click.self="emit('close')">
    <section class="corner-panel" role="dialog" aria-modal="true" aria-label="我们的角落">
      <header>
        <div>
          <span class="eyebrow">OUR LITTLE CORNER</span>
          <h2>我们的角落</h2>
          <p>这里摆的都是我们一起真实做成的事</p>
        </div>
        <button class="close" title="关闭" @click="emit('close')">×</button>
      </header>
      <div class="body">
        <p v-if="loading" class="empty">正在整理角落…</p>
        <p v-else-if="error" class="empty">{{ error }}</p>
        <template v-else>
          <section v-if="lettersAvailable" class="letters" aria-label="写给未来的我们">
            <div class="section-head">
              <h3>写给未来的我们</h3>
              <button class="ghost" @click="toggleCompose()">
                {{ composing ? '不写了' : '写一封' }}
              </button>
            </div>

            <form v-if="composing" class="compose" @submit.prevent="submitLetter">
              <input v-model="draftTitle" class="line" maxlength="60" placeholder="给这封信起个名字（可不填）" />
              <textarea
                v-model="draftBody"
                rows="4"
                maxlength="4000"
                placeholder="写给未来双方的信。只写你自己想说的话，菟菚不会代笔。"
              />
              <div class="unlock-picker" role="radiogroup" aria-label="解锁条件">
                <label><input v-model="draftType" type="radio" value="date" />到日子拆</label>
                <label><input v-model="draftType" type="radio" value="goal" />等目标完成</label>
                <label><input v-model="draftType" type="radio" value="event" />等一件事发生</label>
              </div>
              <input
                v-if="draftType === 'date'"
                v-model="draftDate"
                type="datetime-local"
                aria-label="拆信时间"
              />
              <select v-else-if="draftType === 'goal'" v-model="draftGoalId" aria-label="选择共同目标">
                <option disabled value="">{{ hasActiveGoal ? '选一个还在进行的共同目标' : '还没有进行中的共同目标' }}</option>
                <option v-for="goal in goalOptions" :key="goal.id" :value="goal.id">{{ goal.title }}</option>
              </select>
              <select v-else v-model="draftEventType" aria-label="选择等待的事件">
                <option disabled value="">选一件等它发生的事</option>
                <option v-for="item in eventTypes" :key="item.type" :value="item.type">{{ item.label }}</option>
              </select>
              <p v-if="formError" class="form-error">{{ formError }}</p>
              <button class="primary" type="submit" :disabled="busy">封存这封信</button>
            </form>

            <p v-if="!letters.length" class="empty letters-empty">
              还没有写给未来的信——想对以后{{ props.personaName || '她' }}和你说的话，可以先封存在这里
            </p>
            <article
              v-for="letter in letters"
              :key="letter.id"
              class="letter-card"
              :data-status="letter.status"
            >
              <div class="card-head">
                <span class="state">{{ statusLabel(letter.status) }}</span>
                <button class="delete" @click="removeLetter(letter)">
                  {{ confirmDeleteId === letter.id ? '确认删除' : '删除' }}
                </button>
              </div>
              <h4>{{ letter.title || '一封写给未来的信' }}</h4>
              <p class="condition">{{ conditionText(letter) }}</p>
              <template v-if="letter.status === 'opened'">
                <p class="letter-body">{{ letter.body }}</p>
                <small v-if="letter.opened_at" class="opened-at">拆于 {{ letter.opened_at.slice(0, 10) }}</small>
              </template>
              <button v-else-if="letter.status === 'ready'" class="primary open" @click="openLetter(letter)">
                拆信
              </button>
            </article>
          </section>

          <section v-if="showPages" class="pages" aria-label="我们的纪念页">
            <div class="section-head">
              <h3>我们的纪念页</h3>
            </div>
            <p v-if="snapshotError" class="form-error">{{ snapshotError }}</p>
            <article v-for="page in savedPages" :key="`snap-${page.id}`" class="page-card">
              <div class="card-head">
                <span class="type">第 {{ page.snapshot_days }} 天</span>
                <button class="delete" @click="removeSnapshot(page)">
                  {{ confirmDeleteDays === page.snapshot_days ? '确认删除' : '删除' }}
                </button>
              </div>
              <h4>{{ page.start_date }} → {{ page.cutoff_date }}</h4>
              <p v-if="sourceCountText(page)" class="counts">{{ sourceCountText(page) }}</p>
              <pre class="page-body">{{ page.rendered_markdown }}</pre>
              <small class="generated">整理于 {{ page.generated_at.slice(0, 10) }}</small>
            </article>
            <article
              v-for="milestone in creatablePages"
              :key="`create-${milestone.days}`"
              class="page-card"
            >
              <div class="card-head">
                <span class="type">第 {{ milestone.days }} 天</span>
              </div>
              <p class="page-invite">
                这一页到日子了。要把它整理出来吗？只收集真实发生过的事，随时可以删掉。
              </p>
              <button class="primary" :disabled="snapshotBusy" @click="createPage(milestone.days)">
                整理这一页
              </button>
            </article>
          </section>

          <section v-if="dualsAvailable" class="duals" aria-label="双视角">
            <div class="section-head">
              <h3>双视角 · 同一件事的两种记忆</h3>
              <button class="ghost" @click="toggleDualCompose()">
                {{ dualComposing ? '不写了' : '新建一页' }}
              </button>
            </div>

            <form v-if="dualComposing" class="compose" @submit.prevent="submitDual">
              <input v-model="dualTitle" class="line" maxlength="60" placeholder="这段经历叫什么" />
              <div class="unlock-picker" role="radiogroup" aria-label="锚点类型">
                <label><input v-model="dualAnchorType" type="radio" value="free" />自由主题</label>
                <label><input v-model="dualAnchorType" type="radio" value="event" />真实事件</label>
                <label><input v-model="dualAnchorType" type="radio" value="diary" />某天的日记</label>
                <label><input v-model="dualAnchorType" type="radio" value="goal" />共同目标</label>
              </div>
              <select v-if="dualAnchorType !== 'free'" v-model="dualAnchorId" aria-label="选择经历">
                <option disabled value="">选一段真实经历</option>
                <option v-for="item in anchorOptions()" :key="item.id" :value="item.id">{{ item.label }}</option>
              </select>
              <textarea
                v-model="dualUserView"
                rows="2"
                maxlength="2000"
                placeholder="你记得的版本（现在写或之后补都可以）"
              />
              <p v-if="dualError" class="form-error">{{ dualError }}</p>
              <button class="primary" type="submit" :disabled="dualBusy">留下这一页</button>
            </form>

            <p v-if="!duals.length" class="empty letters-empty">
              同一件事，你和她记得的可能不一样——这里把两个版本都留着
            </p>
            <article v-for="page in duals" :key="`dual-${page.id}`" class="letter-card dual-card">
              <div class="card-head">
                <span class="type">{{ page.source_type === 'free' ? '自由主题' : (page.source_date || '') }}</span>
                <button class="delete" @click="removeDual(page)">
                  {{ confirmDeleteDualId === page.id ? '确认删除' : '删除' }}
                </button>
              </div>
              <h4>{{ page.title }}</h4>
              <div class="dual-cols">
                <div class="view-block">
                  <small>你记得的</small>
                  <p>{{ page.user_view || '（还没写）' }}</p>
                </div>
                <div class="view-block">
                  <small>她记得的 <span v-if="page.tuzhan_view" class="origin">{{ originLabel(page) }}</span></small>
                  <p>{{ page.tuzhan_view || '（她还没说）' }}</p>
                </div>
              </div>
              <div class="dual-actions">
                <button class="ghost" @click="toggleTuzhanEditor(page)">
                  {{ dualDraftTargetId === page.id ? '收起' : '写她的版本' }}
                </button>
                <button class="ghost" :disabled="dualDraftBusyId === page.id" @click="draftTuzhanView(page)">
                  {{ dualDraftBusyId === page.id ? '她在想…' : '请她想一想' }}
                </button>
              </div>
              <template v-if="dualDraftTargetId === page.id">
                <textarea
                  v-model="dualTuzhanDrafts[page.id]"
                  rows="3"
                  maxlength="2000"
                  aria-label="她的版本编辑"
                  @input="dualDraftOrigin[page.id] = 'user'"
                />
                <button
                  class="primary"
                  :disabled="dualSaveBusyId === page.id"
                  @click="saveTuzhanView(page, dualDraftOrigin[page.id] === 'llm' ? 'llm' : 'user')"
                >
                  保存她的版本
                </button>
              </template>
            </article>
            <p v-if="dualError" class="form-error">{{ dualError }}</p>
          </section>

          <section aria-label="一起做成的事">
            <div class="section-head">
              <h3>一起做成的事</h3>
            </div>
            <template v-if="visibleArtifacts.length">
              <article v-for="item in visibleArtifacts" :key="item.id" class="artifact-card">
                <div class="card-head">
                  <span class="type">{{ typeLabel(item.artifact_type) }}</span>
                  <time>{{ item.updated_at.slice(0, 10) }}</time>
                </div>
                <h4>{{ item.title }}</h4>
                <p>{{ item.content }}</p>
                <small v-if="item.version > 1" class="version">已更新 {{ item.version }} 版</small>
              </article>
            </template>
            <p v-else class="empty">
              这里还空着——和{{ props.personaName || '她' }}一起读完第一本书或完成第一个目标，就会留下第一件东西
            </p>
          </section>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.corner-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8,10,16,.6); backdrop-filter: blur(5px); }
.corner-panel { width: min(560px, 94vw); height: 100%; padding: 26px 24px; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: radial-gradient(circle at 80% 0%, color-mix(in srgb, var(--accent) 14%, transparent), transparent 36%), linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -20px 0 55px rgba(0,0,0,.28); }
header { display: flex; align-items: flex-start; justify-content: space-between; }
.eyebrow { color: var(--accent); font-size: 10px; letter-spacing: .19em; }
h2 { margin: 5px 0 6px; font-size: 24px; font-weight: 600; }
header p { margin: 0; color: var(--text-muted); font-size: 12px; }
.close { border: 0; color: var(--text-muted); background: transparent; font-size: 28px; cursor: pointer; }
.body { overflow-y: auto; padding: 14px 2px 50px; }
.section-head { display: flex; align-items: center; justify-content: space-between; margin: 10px 0 12px; }
.section-head h3 { margin: 0; font-size: 13px; font-weight: 600; color: var(--text-muted); letter-spacing: .06em; }
.ghost { border: 1px solid var(--border); border-radius: 99px; padding: 3px 12px; background: transparent; color: var(--text-muted); font-size: 11px; cursor: pointer; }
.compose { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; padding: 12px 14px; border: 1px dashed var(--border); border-radius: 14px; }
.compose input.line, .compose textarea, .compose select, .compose input[type="datetime-local"] { width: 100%; padding: 7px 10px; border: 1px solid var(--border); border-radius: 10px; background: color-mix(in srgb, var(--bg-main) 70%, transparent); color: var(--text); font-size: 12px; box-sizing: border-box; }
.compose textarea { resize: vertical; line-height: 1.7; }
.unlock-picker { display: flex; gap: 12px; font-size: 11px; color: var(--text-muted); }
.unlock-picker label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.form-error { margin: 0; color: #d47a7a; font-size: 11px; }
.primary { align-self: flex-start; border: 0; border-radius: 99px; padding: 6px 16px; background: var(--accent); color: var(--bg-main); font-size: 12px; cursor: pointer; }
.primary:disabled { opacity: .5; cursor: default; }
.letter-card { margin-bottom: 12px; padding: 12px 16px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
.letter-card[data-status="ready"] { border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.state { padding: 2px 8px; border-radius: 99px; color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); font-size: 10px; }
.letter-card h4 { margin: 0 0 6px; font-size: 14px; font-weight: 550; }
.condition { margin: 0; color: var(--text-muted); font-size: 11px; }
.letter-body { margin: 10px 0 0; white-space: pre-wrap; color: var(--text); font-size: 12px; line-height: 1.8; }
.opened-at { display: block; margin-top: 8px; color: var(--text-muted); font-size: 10px; }
.open { margin-top: 10px; }
.delete { border: 0; background: transparent; color: var(--text-muted); font-size: 10px; cursor: pointer; }
.artifact-card { margin-bottom: 12px; padding: 14px 16px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
.type { padding: 2px 8px; border-radius: 99px; color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); font-size: 10px; }
time { color: var(--text-muted); font-size: 10px; }
.artifact-card h4 { margin: 0 0 8px; font-size: 14px; font-weight: 550; }
.artifact-card p { margin: 0; white-space: pre-wrap; color: var(--text); font-size: 12px; line-height: 1.8; }
.version { display: block; margin-top: 8px; color: var(--text-muted); font-size: 10px; }
.page-card { margin-bottom: 12px; padding: 12px 16px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
.page-card h4 { margin: 0 0 6px; font-size: 14px; font-weight: 550; }
.page-card .counts { margin: 0 0 8px; color: var(--text-muted); font-size: 11px; }
.page-body { margin: 0; padding: 10px 12px; border-radius: 10px; background: color-mix(in srgb, var(--bg-main) 70%, transparent); color: var(--text); font-size: 11px; line-height: 1.7; font-family: inherit; white-space: pre-wrap; word-break: break-word; }
.page-invite { margin: 0 0 10px; color: var(--text-muted); font-size: 12px; line-height: 1.7; }
.generated { display: block; margin-top: 8px; color: var(--text-muted); font-size: 10px; }
.empty { color: var(--text-muted); text-align: center; padding: 60px 0; }
.letters-empty { padding: 24px 0; }
.dual-card textarea { width: 100%; margin-top: 8px; padding: 7px 10px; border: 1px solid var(--border); border-radius: 10px; background: color-mix(in srgb, var(--bg-main) 70%, transparent); color: var(--text); font-size: 12px; line-height: 1.7; box-sizing: border-box; resize: vertical; }
.dual-actions { display: flex; gap: 8px; margin-top: 8px; }
.dual-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 8px; }
.view-block { padding: 8px 10px; border-radius: 10px; background: color-mix(in srgb, var(--bg-main) 70%, transparent); }
.view-block small { display: block; margin-bottom: 4px; color: var(--text-muted); font-size: 10px; }
.view-block p { margin: 0; white-space: pre-wrap; color: var(--text); font-size: 12px; line-height: 1.7; }
.origin { padding: 1px 6px; border-radius: 99px; color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); font-size: 9px; }
</style>
