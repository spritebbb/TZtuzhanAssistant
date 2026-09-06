<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { listArtifacts, type ArtifactItem } from '../api/artifacts'
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
  const [artifactResult, lettersResult, snapshotsResult] = await Promise.allSettled([
    listArtifacts(),
    listFutureLetters(),
    listRelationshipSnapshots(),
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
</style>
