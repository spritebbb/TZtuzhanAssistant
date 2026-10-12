<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  cancelReading,
  completeReading,
  exportReadingUrl,
  listReadingActivities,
  pauseReading,
  proposeReadingQuestion,
  resumeReading,
  saveReadingNote,
  saveReadingViewpoint,
  finishSegment,
  getBookmarkDraft,
  getReadingMap,
  saveBookmark,
  setReadingPosition,
  startReading,
  type ReadingActivity,
  type ReadingMap,
  type ViewpointRole,
} from '../api/activities'
import { listKnowledgeDocuments, type KnowledgeDocument } from '../api/knowledge'
import ViewpointBlock from './ViewpointBlock.vue'
import {
  addWritingTurn,
  cancelWriting,
  confirmOutline,
  getOutlineDraft,
  completeWriting,
  exportWritingUrl,
  listWritings,
  pauseWriting,
  requestTuzhanTurn,
  resumeWriting,
  startWriting,
  type CoWriting,
  type OutlineDraft,
} from '../api/writings'
import {
  addObservationEntry,
  cancelObservation,
  completeObservation,
  listObservations,
  startObservation,
  type Observation,
} from '../api/observations'
const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-bookshelf'): void
  (e: 'discuss', draft: string): void
}>()

const activities = ref<ReadingActivity[]>([])
const documents = ref<KnowledgeDocument[]>([])
const current = ref<ReadingActivity | null>(null)
const writings = ref<CoWriting[]>([])
const currentWriting = ref<CoWriting | null>(null)
const showWritingForm = ref(false)
const writingTitle = ref('')
const writingPremise = ref('')
const writingTurnDraft = ref('')
const keepCoStory = ref(true)
const writingBusy = ref(false)
const writingSubtype = ref<'story' | 'world' | 'character'>('story')
// L02 结构化大纲（世界观/角色设定）
const outlineDraft = ref<OutlineDraft | null>(null)
const outlineText = ref<Record<string, string>>({})
// L02 观察日志
const observations = ref<Observation[]>([])
const observationTitle = ref('')
const observationEntry = ref('')
const currentObservation = ref<Observation | null>(null)
const noteDraft = ref('')
const viewpointDrafts = ref<Record<ViewpointRole, string>>({ user: '', tuzhan: '', shared: '' })
const loading = ref(false)
const busy = ref(false)
// F05 阅读地图：只在打开共读详情时按需拉取
const readingMap = ref<ReadingMap | null>(null)
const bookmarkDraftText = ref('')
const error = ref('')
const notice = ref('')

const viewpointRoles = computed(() => [
  { role: 'user' as const, label: '我的看法', placeholder: '这一段你自己怎么看，记下来就是你的' },
  { role: 'tuzhan' as const, label: `${props.personaName || '她'}的看法`, placeholder: '她刚才聊到的角度，用她的话记一句' },
  { role: 'shared' as const, label: '共同结论', placeholder: '你们都点头的那一句' },
])

const unfinished = computed(() => activities.value.filter(
  item => item.status === 'active' || item.status === 'paused',
))
const completed = computed(() => activities.value.filter(
  item => item.status === 'completed' || item.status === 'cancelled',
).slice(0, 4))
const openWritings = computed(() => writings.value.filter(item => item.status === 'active' || item.status === 'paused'))
const completedWritings = computed(() => writings.value.filter(item => item.status === 'completed').slice(0, 4))

function serverViewpoint(role: ViewpointRole): string {
  if (!current.value) return ''
  const position = current.value.status === 'completed' ? -1 : current.value.position
  const viewpoints = current.value.viewpoints ?? []
  return (
    viewpoints.find(
      item => item.role === role && item.position === position,
    )?.content ?? ''
  )
}

function syncViewpointDrafts() {
  for (const role of ['user', 'tuzhan', 'shared'] as ViewpointRole[]) {
    viewpointDrafts.value[role] = serverViewpoint(role)
  }
}

watch(
  () => [
    current.value?.id,
    current.value && current.value.status !== 'completed' ? current.value.position : -1,
  ],
  () => syncViewpointDrafts(),
  { immediate: true },
)

function applyCurrent(value: ReadingActivity) {
  current.value = value
  noteDraft.value = value.note
  activities.value = [value, ...activities.value.filter(item => item.id !== value.id)]
}

async function load() {
  loading.value = true
  error.value = ''
  notice.value = ''
  try {
    const [activityRows, documentRows, writingRows] = await Promise.all([
      listReadingActivities(),
      listKnowledgeDocuments(),
      listWritings(),
    ])
    activities.value = activityRows
    documents.value = documentRows
    writings.value = writingRows
    observations.value = await listObservations()
    const active = activityRows.find(item => item.status === 'active') ?? null
    current.value = active
    noteDraft.value = active?.note ?? ''
  } catch {
    error.value = '共读内容加载失败，请稍后再试'
  } finally {
    loading.value = false
  }
}

async function run(action: () => Promise<ReadingActivity>, success = '') {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const next = await action()
    applyCurrent(next)
    // F05：共读详情变化后刷新阅读地图（失败静默，不阻塞主流程）
    void loadReadingMap(next.id)
    notice.value = success
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '这次没有记上，再试一次'
  } finally {
    busy.value = false
  }
}

function begin(documentId: number) {
  void run(() => startReading(documentId), '书翻开了，从这里一起读')
}

function resume(item: ReadingActivity) {
  void run(() => resumeReading(item.id), '又回到上次停下的地方')
}

function move(offset: number) {
  if (!current.value) return
  const next = current.value.position + offset
  if (next < 0 || next >= current.value.total) return
  void run(() => setReadingPosition(current.value!.id, next))
}

function saveNote() {
  if (!current.value) return
  void run(() => saveReadingNote(current.value!.id, noteDraft.value), '这张书签夹好了')
}

async function saveViewpoints() {
  if (!current.value || busy.value) return
  const id = current.value.id
  const changed = viewpointRoles.value.filter(
    vp => viewpointDrafts.value[vp.role].trim() !== serverViewpoint(vp.role),
  )
  if (!changed.length) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    for (const vp of changed) {
      current.value = await saveReadingViewpoint(id, vp.role, viewpointDrafts.value[vp.role])
    }
    activities.value = [current.value, ...activities.value.filter(item => item.id !== id)]
    notice.value = '这段的看法记下了'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '这次没有记上，再试一次'
  } finally {
    busy.value = false
  }
}

async function discuss() {
  if (!current.value || busy.value) return
  if (noteDraft.value.trim() !== current.value.note) {
    await run(() => saveReadingNote(current.value!.id, noteDraft.value))
    if (error.value) return
  }
  const item = current.value
  const myTake = viewpointDrafts.value.user.trim()
  busy.value = true
  error.value = ''
  try {
    const question = await proposeReadingQuestion(item.id, myTake)
    const answerLead = myTake ? `我的看法是：${myTake}` : '我的想法是：'
    emit('discuss', `关于《${item.filename}》第 ${item.position + 1} 段，你问我：“${question}”\n${answerLead}`)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '这一段的问题暂时没想出来'
  } finally {
    busy.value = false
  }
}

function finish() {
  if (!current.value) return
  void run(() => completeReading(current.value!.id), '这本书读完了，但话还没聊完')
}

function pauseCurrentReading() {
  if (!current.value) return
  void run(() => pauseReading(current.value!.id), '书签夹在这里，回来接着读')
}

function resumeCurrentReading() {
  if (!current.value) return
  void run(() => resumeReading(current.value!.id), '又回到上次停下的地方')
}

function cancelCurrentReading() {
  if (!current.value) return
  void run(() => cancelReading(current.value!.id), '这场共读已经放下，记录还留着')
}

function applyWriting(value: CoWriting) {
  currentWriting.value = value
  writings.value = [value, ...writings.value.filter(item => item.id !== value.id)]
}

async function runWriting(action: () => Promise<CoWriting>, success = '') {
  if (busy.value) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    applyWriting(await action())
    notice.value = success
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '这次没有记上，再试一次'
  } finally {
    busy.value = false
  }
}

async function loadOutlineDraft(activityId: number) {
  try {
    const draft = await getOutlineDraft(activityId)
    outlineDraft.value = draft
    const text: Record<string, string> = {}
    for (const field of draft.fields) text[field] = (draft.draft[field] || []).join(String.fromCharCode(10))
    outlineText.value = text
  } catch {
    outlineDraft.value = null
  }
}

async function saveOutline() {
  if (!currentWriting.value || !outlineDraft.value) return
  const outline: Record<string, string[]> = {}
  for (const field of outlineDraft.value.fields) {
    outline[field] = (outlineText.value[field] || '').split(String.fromCharCode(10))
  }
  try {
    outlineDraft.value = await confirmOutline(
      currentWriting.value.id, outline, outlineDraft.value.version)
    notice.value = '大纲已保存'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '大纲保存失败'
  }
}

async function createObservation() {
  const title = observationTitle.value.trim()
  if (!title || busy.value) return
  busy.value = true
  try {
    const created = await startObservation(title)
    observations.value = [created, ...observations.value]
    observationTitle.value = ''
    currentObservation.value = created
    notice.value = '这本日志开始了'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '开始记录失败'
  } finally {
    busy.value = false
  }
}

async function openObservation(item: Observation) {
  currentObservation.value = item
  observationEntry.value = ''
}

async function addObservation() {
  if (!currentObservation.value || !observationEntry.value.trim()) return
  busy.value = true
  try {
    currentObservation.value = await addObservationEntry(
      currentObservation.value.id, observationEntry.value.trim())
    observationEntry.value = ''
  } catch (e) {
    error.value = e instanceof Error ? e.message : '记录失败'
  } finally {
    busy.value = false
  }
}

async function finishObservation() {
  if (!currentObservation.value) return
  busy.value = true
  try {
    currentObservation.value = await completeObservation(currentObservation.value.id)
    notice.value = '这本日志收好了'
    observations.value = await listObservations()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '收尾失败'
  } finally {
    busy.value = false
  }
}

async function dropObservation() {
  if (!currentObservation.value) return
  busy.value = true
  try {
    await cancelObservation(currentObservation.value.id)
    currentObservation.value = null
    observations.value = await listObservations()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '放下失败'
  } finally {
    busy.value = false
  }
}

async function createWriting() {
  await runWriting(
    () => startWriting(writingTitle.value, writingPremise.value, writingSubtype.value),
    '开头留下了，先写第一段',
  )
  if (!error.value) {
    showWritingForm.value = false
    writingTitle.value = ''
    writingPremise.value = ''
  }
}

function openWriting(item: CoWriting) {
  currentWriting.value = item
  writingTurnDraft.value = ''
  notice.value = ''
  error.value = ''
}

// P2-18：先 await 请求结果、成功才清稿——旧实现同步前缀刚置 error 空就
// 挂起，下一行立即判空清稿，失败时用户刚写的话已经丢了
async function submitTurn() {
  if (!currentWriting.value || busy.value || !writingTurnDraft.value.trim()) return
  const text = writingTurnDraft.value
  await runWriting(() => addWritingTurn(currentWriting.value!.id, text), '这一段写下去了')
  if (!error.value) writingTurnDraft.value = ''
}

async function askTuzhanTurn() {
  if (!currentWriting.value || writingBusy.value) return
  writingBusy.value = true
  error.value = ''
  notice.value = ''
  try {
    applyWriting(await requestTuzhanTurn(currentWriting.value.id))
    notice.value = '她接了一段'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '她这轮没接上，再试一次'
  } finally {
    writingBusy.value = false
  }
}

function leaveWriting() {
  currentWriting.value = null
  writingTurnDraft.value = ''
  notice.value = ''
  error.value = ''
}

function pauseCurrentWriting() {
  const item = currentWriting.value
  if (item) void runWriting(() => pauseWriting(item.id), '先放在这里，回来接着写')
}

function resumeCurrentWriting() {
  const item = currentWriting.value
  if (item) void runWriting(() => resumeWriting(item.id), '接着往下写')
}

function finishCurrentWriting() {
  const item = currentWriting.value
  if (item) void runWriting(() => completeWriting(item.id, keepCoStory.value), '故事收笔，收进了共同角落')
}

function cancelCurrentWriting() {
  const item = currentWriting.value
  if (item) void runWriting(() => cancelWriting(item.id), '这个故事先不写了')
}

async function loadReadingMap(activityId: number) {
  try {
    readingMap.value = await getReadingMap(activityId)
  } catch {
    readingMap.value = null
  }
}

async function markSegmentRead(index: number, hash: string) {
  if (!current.value) return
  busy.value = true
  try {
    readingMap.value = await finishSegment(current.value.id, index, hash)
    notice.value = '这一段读完了，下一段已经打开'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '完成这一段失败'
  } finally {
    busy.value = false
  }
}

async function draftBookmark(segmentId: number) {
  if (!current.value) return
  busy.value = true
  try {
    const draft = await getBookmarkDraft(current.value.id, segmentId)
    bookmarkDraftText.value = draft.user_view || draft.excerpt
    notice.value = '草稿已生成，改完再确认'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '草稿生成失败'
  } finally {
    busy.value = false
  }
}

async function confirmBookmark(segmentId: number) {
  if (!current.value) return
  busy.value = true
  try {
    await saveBookmark(current.value.id, segmentId, { user_view: bookmarkDraftText.value })
    readingMap.value = await getReadingMap(current.value.id)
    bookmarkDraftText.value = ''
    notice.value = '书签已收好'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '书签保存失败'
  } finally {
    busy.value = false
  }
}

function leaveCurrent() {
  current.value = null
  noteDraft.value = ''
  notice.value = ''
  error.value = ''
}

watch(() => props.show, show => { if (show) void load() }, { immediate: true })
</script>

<template>
  <div v-if="show" class="activity-mask" @click.self="emit('close')">
    <section class="activity-panel" role="dialog" aria-modal="true" aria-label="一起做点什么">
      <header>
        <div>
          <span class="eyebrow">DOING THINGS TOGETHER</span>
          <h2>一起做点什么</h2>
          <p>共读、专注、写故事，也把想做成的事一步步留在这里</p>
        </div>
        <button class="close" aria-label="关闭活动面板" @click="emit('close')">×</button>
      </header>

      <div class="body">
        <p v-if="loading" class="empty">正在找上次夹的书签…</p>
        <p v-else-if="error && !current && !currentWriting" class="empty error">{{ error }}</p>

        <template v-else-if="currentWriting">
          <button class="back" @click="leaveWriting">← 所有活动</button>
          <div class="goal-head">
            <div>
              <span class="status" :class="currentWriting.status">
                {{ currentWriting.status === 'completed' ? '已收笔' : currentWriting.status === 'paused' ? '暂放一下' : currentWriting.status === 'cancelled' ? '已放下' : '一起写着' }}
              </span>
              <h3>{{ currentWriting.title }}</h3>
              <p v-if="currentWriting.premise">开头设定：{{ currentWriting.premise }}</p>
            </div>
          </div>

            <div v-if="currentWriting && currentWriting.subtype && currentWriting.subtype !== 'story'" class="outline-block">
              <span class="vp-title">结构化大纲</span>
              <button class="secondary" :disabled="busy" @click="loadOutlineDraft(currentWriting.id)">生成大纲草稿</button>
              <template v-if="outlineDraft">
                <label v-for="field in outlineDraft.fields" :key="field">
                  <span>{{ field }}</span>
                  <textarea v-model="outlineText[field]" rows="2" :placeholder="'一行一条'"></textarea>
                </label>
                <button class="talk" :disabled="busy" @click="saveOutline">保存大纲</button>
              </template>
            </div>

          <div class="story-turns">
            <article v-for="turn in currentWriting.turns" :key="turn.id" :class="turn.author">
              <span>{{ turn.author === 'user' ? '我' : props.personaName || '她' }}</span>
              <p>{{ turn.content }}</p>
            </article>
            <p v-if="!currentWriting.turns.length" class="empty small">故事还没有正文，写下开头第一段</p>
          </div>

          <template v-if="currentWriting.status === 'active' || currentWriting.status === 'paused'">
            <label class="writing-turn-form">
              <span>写下你的一段</span>
              <textarea v-model="writingTurnDraft" rows="3" maxlength="2000" placeholder="接住上一段的情节，往下写两三句"></textarea>
            </label>
            <div class="notice-line"><span :class="{ error: !!error }">{{ error || notice }}</span></div>
            <div class="goal-actions writing-actions">
              <button :disabled="busy || !writingTurnDraft.trim()" @click="submitTurn">记下这一段</button>
              <button :disabled="writingBusy || !currentWriting.turns.length" @click="askTuzhanTurn">{{ writingBusy ? '她在写…' : `请${props.personaName || '她'}续写一段` }}</button>
            </div>
            <div class="goal-actions">
              <button v-if="currentWriting.status === 'active'" :disabled="busy" @click="pauseCurrentWriting">暂停</button>
              <button v-else :disabled="busy" @click="resumeCurrentWriting">继续</button>
              <label class="keepsake-choice"><input v-model="keepCoStory" type="checkbox">收笔时把故事存进共同角落</label>
              <button class="finish" :disabled="busy" @click="finishCurrentWriting">故事收笔</button>
              <button class="plain-action" :disabled="busy" @click="cancelCurrentWriting">放下故事</button>
            </div>
          </template>
          <div v-else class="notice-line"><span :class="{ error: !!error }">{{ error || notice }}</span></div>

          <article v-if="currentWriting.story" class="summary-box"><span>共同故事</span><p>{{ currentWriting.story }}</p></article>
          <a class="goal-export" :href="exportWritingUrl(currentWriting.id)" download>导出 Markdown</a>
          <p class="fiction-note">这是一段你们共同虚构的创作；故事只留在故事里，不会变成你们现实记忆的一部分。</p>
          <ViewpointBlock :activity-id="currentWriting.id" :persona-name="personaName" />
        </template>

        <template v-else-if="current">
          <button class="back" @click="leaveCurrent">← 共读书架</button>
          <div class="reading-head">
            <div>
              <span class="status" :class="current.status">{{ current.status === 'completed' ? '已读完' : current.status === 'paused' ? '已暂停' : current.status === 'cancelled' ? '已放下' : '共读中' }}</span>
              <h3>{{ current.filename }}</h3>
              <p>第 {{ current.position + 1 }} / {{ current.total }} 段 · 已留 {{ current.note_count }} 张书签</p>
            </div>
            <strong>{{ current.progress }}%</strong>
          </div>
          <div class="progress"><span :style="{ width: current.progress + '%' }"></span></div>

          <section v-if="readingMap && readingMap.total" class="reading-map">
            <div class="map-head">
              <span>阅读地图</span>
              <small>读完 {{ readingMap.read_count }} / {{ readingMap.total }} 段 · 确认书签 {{ readingMap.confirmed_bookmarks }}</small>
            </div>
            <ul>
              <li v-for="seg in readingMap.segments" :key="seg.id" :class="seg.status">
                <span class="seg-title">{{ seg.title }}</span>
                <span class="seg-state">
                  {{ seg.status === 'read' ? '已读' : seg.status === 'current' ? '当前' : seg.status === 'legacy_position' ? '旧位置' : '未解锁' }}
                </span>
                <button
                  v-if="seg.status === 'current' && current.status !== 'completed'"
                  :disabled="busy" @click="markSegmentRead(seg.segment_index, seg.source_hash)"
                >读完这段</button>
                <template v-if="seg.status === 'current' || seg.status === 'read'">
                  <button :disabled="busy" class="ghost" @click="draftBookmark(seg.id)">生成草稿</button>
                  <input
                    v-if="seg.status === 'current'" v-model="bookmarkDraftText"
                    class="draft-input" maxlength="600" placeholder="这一段的看法，确认后收好"
                  />
                  <button
                    v-if="seg.status === 'current' && bookmarkDraftText"
                    :disabled="busy" @click="confirmBookmark(seg.id)"
                  >确认书签</button>
                  <em v-if="seg.bookmark && seg.bookmark.status === 'confirmed'">已确认</em>
                </template>
              </li>
            </ul>
          </section>

          <article class="excerpt">
            <span class="quote-mark">“</span>
            <p>{{ current.excerpt || '这一段暂时没有可读文字' }}</p>
          </article>

          <template v-if="current.status === 'active' || current.status === 'paused'">
            <div class="page-actions">
              <button :disabled="busy || current.position === 0" @click="move(-1)">上一段</button>
              <span>慢慢读，不赶进度</span>
              <button :disabled="busy || current.position >= current.total - 1" @click="move(1)">下一段</button>
            </div>

            <label class="note-box">
              <span>这一段的书签</span>
              <textarea v-model="noteDraft" rows="3" maxlength="2000" placeholder="写下一句想法，下次回来还在这里"></textarea>
            </label>

            <div class="viewpoints">
              <span class="vp-title">这一段，各自的看法</span>
              <label v-for="vp in viewpointRoles" :key="vp.role">
                <span>{{ vp.label }}</span>
                <textarea
                  v-model="viewpointDrafts[vp.role]"
                  rows="2" maxlength="2000" :placeholder="vp.placeholder"
                ></textarea>
              </label>
              <button class="secondary" :disabled="busy" @click="saveViewpoints">记下我们的看法</button>
            </div>
            <div class="notice-line">
              <span :class="{ error: !!error }">{{ error || notice }}</span>
              <button class="secondary" :disabled="busy" @click="saveNote">收进书签</button>
            </div>

            <div class="primary-actions">
              <button v-if="current.status === 'active'" class="talk" :disabled="busy" @click="discuss">让{{ props.personaName || '助手' }}问个具体问题</button>
              <button v-if="current.status === 'active'" :disabled="busy" @click="pauseCurrentReading">暂停</button>
              <button v-else :disabled="busy" @click="resumeCurrentReading">继续</button>
              <button class="finish" :disabled="busy" @click="finish">这本读完了</button>
              <button class="plain-action" :disabled="busy" @click="cancelCurrentReading">放下这本</button>
            </div>
          </template>
          <div v-else class="completed-card">
            <span>✶</span>
            <div><strong>{{ current.status === 'completed' ? '一起读到了最后' : '这场共读已经放下' }}</strong><p>{{ notice || '书签和双方看法仍会留在这里' }}</p></div>
            <button @click="begin(current.document_id)">再读一遍</button>
          </div>
          <article v-if="current.summary" class="summary-box">
            <span>共同书摘</span>
            <p>{{ current.summary }}</p>
          </article>
          <a class="goal-export" :href="exportReadingUrl(current.id)" download>导出 Markdown</a>
        </template>

        <template v-else>
          <section class="shelf-section goal-section writing-section">
            <div class="section-title"><span>STORY RELAY</span><h3>共同创作</h3></div>
            <p class="focus-hint">轮流各写一段，把一个虚构故事接着讲下去；故事只是故事，不会变成你们的记忆</p>
            <button v-if="!showWritingForm" class="open-bookshelf" @click="showWritingForm = true">开一个新故事</button>
            <div v-else class="goal-create">
              <label><span>类型</span>
                <select v-model="writingSubtype">
                  <option value="story">故事</option>
                  <option value="world">世界观</option>
                  <option value="character">角色设定</option>
                </select>
              </label>
              <label><span>{{ writingSubtype === 'story' ? '故事名' : writingSubtype === 'world' ? '世界名' : '角色名' }}</span><input v-model="writingTitle" maxlength="120" placeholder="例如：灯塔看守人的猫"></label>
              <label><span>开头设定（可选）</span><textarea v-model="writingPremise" rows="2" maxlength="500" placeholder="一句话设定题材或世界观"></textarea></label>
              <div class="goal-create-actions"><button class="plain-action" @click="showWritingForm = false">取消</button><button class="talk" :disabled="busy || !writingTitle.trim()" @click="createWriting">开始</button></div>
            </div>
            <div v-if="openWritings.length" class="activity-list goal-list">
              <button v-for="item in openWritings" :key="item.id" @click="openWriting(item)">
                <span class="format">STORY</span><span><strong>{{ item.title }}</strong><small>{{ item.turns.length }} 段 · {{ item.status === 'paused' ? '暂放一下' : '一起写着' }}</small></span><em>{{ item.status === 'paused' ? '继续' : '查看' }}</em>
              </button>
            </div>
            <div v-if="completedWritings.length" class="activity-list goal-list completed-goals">
              <button v-for="item in completedWritings" :key="item.id" @click="openWriting(item)"><span>✓</span><span><strong>{{ item.title }}</strong><small>{{ item.turns.length }} 段故事</small></span><em>回看</em></button>
            </div>
          </section>

          <section class="shelf-section goal-section writing-section">
            <div class="section-title"><span>OBSERVATION</span><h3>观察日志</h3></div>
            <p class="focus-hint">只记真实看到的事，来源可追溯；没有来源的条目会如实标注</p>
            <div class="goal-create">
              <label><span>日志名</span><input v-model="observationTitle" maxlength="60" placeholder="例如：楼下那棵树"></label>
              <div class="goal-create-actions">
                <button class="talk" :disabled="busy || !observationTitle.trim()" @click="createObservation">开始记录</button>
              </div>
            </div>
            <div v-if="observations.length" class="activity-list goal-list">
              <button v-for="item in observations" :key="item.id" @click="openObservation(item)">
                <span class="format">LOG</span>
                <span><strong>{{ item.title }}</strong><small>{{ item.status === 'completed' ? '已收好' : item.status === 'cancelled' ? '已放下' : '在记' }}</small></span>
                <em>查看</em>
              </button>
            </div>
            <div v-if="currentObservation" class="observation-detail">
              <h4>{{ currentObservation.title }}</h4>
              <ul v-if="currentObservation.entries && currentObservation.entries.length">
                <li v-for="entry in currentObservation.entries" :key="entry.id">
                  <small>{{ entry.observed_at.slice(0, 10) }} · {{ entry.observer === 'user' ? '我' : '她' }}</small>
                  <span>{{ entry.content }}</span>
                  <em v-if="entry.source_type">来源 {{ entry.source_type }}#{{ entry.source_id }}</em>
                </li>
              </ul>
              <p v-else class="focus-hint">还没记下任何观察</p>
              <template v-if="currentObservation.status === 'active'">
                <textarea v-model="observationEntry" rows="2" maxlength="400" placeholder="今天观察到什么"></textarea>
                <div class="goal-create-actions">
                  <button class="plain-action" :disabled="busy || !observationEntry.trim()" @click="addObservation">记一条</button>
                  <button class="talk" :disabled="busy" @click="finishObservation">收好这本</button>
                  <button class="plain-action" :disabled="busy" @click="dropObservation">放下</button>
                </div>
              </template>
              <pre v-else-if="currentObservation.log" class="observation-log">{{ currentObservation.log }}</pre>
            </div>
          </section>

          <section v-if="unfinished.length" class="shelf-section">
            <div class="section-title"><span>BOOKMARKS</span><h3>接着上次读</h3></div>
            <div class="activity-list">
              <button v-for="item in unfinished" :key="item.id" @click="resume(item)">
                <span class="format">{{ item.format }}</span>
                <span><strong>{{ item.filename }}</strong><small>第 {{ item.position + 1 }}/{{ item.total }} 段 · {{ item.progress }}%</small></span>
                <em>继续</em>
              </button>
            </div>
          </section>

          <section class="shelf-section">
            <div class="section-title"><span>START A BOOK</span><h3>从{{ props.personaName || '助手' }}的书架选一份</h3></div>
            <p v-if="!documents.length" class="empty small">书架还空着，先给{{ props.personaName || '助手' }}一份想一起读的文档</p>
            <div v-else class="document-grid">
              <button v-for="doc in documents" :key="doc.id" :disabled="busy" @click="begin(doc.id)">
                <span>{{ doc.format }}</span>
                <strong :title="doc.filename">{{ doc.filename }}</strong>
                <small>{{ doc.chunk_count }} 段</small>
              </button>
            </div>
            <button v-if="!documents.length" class="open-bookshelf" @click="emit('open-bookshelf')">打开{{ props.personaName || '助手' }}的书架</button>
          </section>

          <section v-if="completed.length" class="shelf-section history">
            <div class="section-title"><span>HISTORY</span><h3>一起读过</h3></div>
            <div class="activity-list">
              <button v-for="item in completed" :key="item.id" class="history-row" :title="item.summary ? '回看共同书摘' : item.status === 'cancelled' ? '回看已放下的记录' : '已读完'" @click="current = item">
                <span>{{ item.status === 'cancelled' ? '—' : '✓' }}</span><strong>{{ item.filename }}</strong><small>{{ item.status === 'cancelled' ? '已放下 · ' : item.summary ? '有书摘 · ' : '' }}{{ item.note_count }} 张书签</small>
              </button>
            </div>
          </section>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.focus-card { padding: 14px 16px; border: 1px solid var(--border); border-radius: 12px; background: color-mix(in srgb, var(--bg-card) 82%, transparent); }
.focus-clock { display: flex; align-items: baseline; gap: 12px; }
.focus-clock strong { font-size: 30px; font-variant-numeric: tabular-nums; letter-spacing: .04em; }
.focus-clock span { color: var(--text-muted); font-size: 12px; }
.focus-hint { margin: 8px 0 12px; color: var(--text-muted); font-size: 12px; }
.focus-actions { display: flex; gap: 10px; }
.focus-actions button { padding: 7px 18px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: var(--bg-card); cursor: pointer; }
.focus-actions button:disabled { opacity: .5; cursor: default; }
.focus-actions .plain { border-color: transparent; color: var(--text-muted); background: transparent; }
.outline-block { display: flex; flex-direction: column; gap: 6px; margin: 10px 0; }
.outline-block label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.observation-detail { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.observation-detail ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.observation-detail li { display: flex; gap: 8px; font-size: 12px; }
.observation-log { white-space: pre-wrap; font-size: 12px; color: var(--text-muted); }
.goal-section, .writing-section { padding: 16px; border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border)); border-radius: 16px; background: color-mix(in srgb, var(--accent) 5%, transparent); }
.goal-create, .goal-progress-form { margin-top: 12px; display: grid; gap: 10px; }
.goal-create label, .goal-progress-form label { display: grid; gap: 5px; color: var(--text-muted); font-size: 10px; }
.goal-create input, .goal-create textarea, .goal-progress-form input, .goal-progress-form textarea { box-sizing: border-box; width: 100%; padding: 9px 11px; border: 1px solid var(--border); border-radius: 10px; outline: none; color: var(--text); background: var(--bg-card); font: inherit; font-size: 12px; }
.goal-create textarea, .goal-progress-form textarea { resize: vertical; }
.support-choice { display: flex; flex-wrap: wrap; gap: 16px; }
.support-choice label { display: flex; align-items: center; gap: 6px; }
.support-choice input, .keepsake-choice input { width: auto; }
.goal-create-actions, .goal-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 9px; }
.plain-action { padding: 8px 10px; border: 0; color: var(--text-muted); background: transparent; cursor: pointer; }
.goal-list { margin-top: 10px; }
.completed-goals { opacity: .82; }
.goal-head h3 { margin: 8px 0 5px; font-size: 22px; }
.goal-head p { margin: 0; color: var(--text-muted); font-size: 12px; line-height: 1.6; }
.next-step-card { margin: 16px 0; padding: 16px; display: grid; gap: 6px; border: 1px solid var(--border); border-radius: 15px; background: color-mix(in srgb, var(--accent) 7%, var(--bg-card)); }
.next-step-card span { color: var(--accent); font-size: 9px; letter-spacing: .15em; }
.next-step-card strong { font-size: 15px; font-weight: 550; }
.next-step-card small { color: var(--text-muted); font-size: 10px; }
.goal-progress-row { display: grid; grid-template-columns: 130px 1fr; gap: 10px; }
.goal-actions { margin: 8px 0 20px; }
.goal-actions > button { padding: 8px 11px; border: 1px solid var(--border); border-radius: 9px; color: var(--text-muted); background: transparent; cursor: pointer; }
.keepsake-choice { margin-right: auto; display: flex; align-items: center; gap: 6px; color: var(--text-muted); font-size: 10px; }
.goal-timeline { margin-top: 18px; }
.goal-timeline article { margin-top: 8px; padding: 10px 12px; border-left: 2px solid color-mix(in srgb, var(--accent) 60%, transparent); background: color-mix(in srgb, var(--bg-card) 80%, transparent); }
.goal-timeline article > span, .goal-timeline article > small { color: var(--text-muted); font-size: 9px; }
.goal-timeline article p { margin: 5px 0; white-space: pre-wrap; font-size: 12px; }
.goal-export { display: inline-block; margin-top: 12px; color: var(--accent); font-size: 11px; text-decoration: none; }
.writing-turn-form { display: block; margin-top: 14px; }
.writing-turn-form > span { color: var(--text-muted); font-size: 11px; }
.writing-turn-form textarea { box-sizing: border-box; width: 100%; margin-top: 7px; padding: 12px 14px; resize: vertical; border: 1px solid var(--border); border-radius: 13px; outline: none; color: var(--text); background: color-mix(in srgb, var(--bg-card) 82%, transparent); font: inherit; font-size: 12px; line-height: 1.6; }
.writing-turn-form textarea:focus { border-color: var(--accent); }
.writing-actions { margin-top: 6px; }
.story-turns { margin-top: 14px; display: grid; gap: 10px; }
.story-turns article { padding: 11px 14px; border: 1px solid var(--border); border-radius: 13px; background: color-mix(in srgb, var(--bg-card) 84%, transparent); }
.story-turns article.user { border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); }
.story-turns article > span { color: var(--accent); font-size: 9px; letter-spacing: .12em; }
.story-turns article p { margin: 5px 0 0; white-space: pre-wrap; font-size: 13px; line-height: 1.8; }
.item-del { margin-top: 8px; }
.writing-turn-form input { box-sizing: border-box; width: 100%; margin-top: 7px; padding: 9px 12px; border: 1px solid var(--border); border-radius: 11px; outline: none; color: var(--text); background: color-mix(in srgb, var(--bg-card) 82%, transparent); font: inherit; font-size: 12px; }
.writing-turn-form input:focus { border-color: var(--accent); }
.fiction-note { margin-top: 14px; color: var(--text-faint); font-size: 10px; line-height: 1.6; }
.activity-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8,10,16,.64); backdrop-filter: blur(6px); }
.activity-panel { width: min(720px, 97vw); height: 100%; padding: 26px 28px; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: radial-gradient(circle at 82% 3%, color-mix(in srgb, var(--accent) 15%, transparent), transparent 34%), linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -24px 0 65px rgba(0,0,0,.3); }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; flex-shrink: 0; }
.eyebrow, .section-title > span { color: var(--accent); font-size: 10px; letter-spacing: .18em; }
h2 { margin: 5px 0 3px; font-size: 26px; font-weight: 600; }
header p, .reading-head p { margin: 0; color: var(--text-muted); font-size: 12px; }
.reading-map { margin: 12px 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); }
.map-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
.map-head small { color: var(--text-muted); font-size: 12px; }
.reading-map ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.reading-map li { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.reading-map li.locked { opacity: .5; }
.seg-title { min-width: 62px; }
.seg-state { color: var(--text-muted); }
.draft-input { flex: 1; min-width: 80px; background: transparent; border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 2px 6px; }
.close { border: 0; color: var(--text-muted); background: transparent; font-size: 28px; cursor: pointer; }
.body { margin-top: 20px; padding: 1px 3px 42px 0; overflow-y: auto; }
.back { margin-bottom: 12px; border: 0; color: var(--text-muted); background: transparent; font-size: 11px; cursor: pointer; }
.reading-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; }
.reading-head h3 { margin: 7px 0 3px; font-size: 21px; font-weight: 600; }
.reading-head > strong { color: var(--accent); font-size: 30px; font-weight: 500; }
.status { padding: 3px 8px; border-radius: 10px; color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); font-size: 10px; }
.status.completed { color: #74b997; }
.progress { height: 4px; margin-top: 13px; overflow: hidden; border-radius: 4px; background: var(--bg-hover); }
.progress span { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--accent), #e4b861); transition: width .25s; }
.excerpt { position: relative; min-height: 190px; margin-top: 18px; padding: 28px 34px; border: 1px solid var(--border); border-radius: 18px; background: color-mix(in srgb, var(--bg-card) 86%, transparent); }
.excerpt::after { content: ''; position: absolute; inset: 10px; pointer-events: none; border: 1px solid color-mix(in srgb, var(--border) 55%, transparent); border-radius: 12px; }
.excerpt p { position: relative; z-index: 1; margin: 0; white-space: pre-wrap; line-height: 1.9; font-size: 14px; }
.quote-mark { position: absolute; top: 5px; left: 16px; color: var(--accent); opacity: .35; font: 42px Georgia, serif; }
.page-actions { display: grid; grid-template-columns: 90px 1fr 90px; align-items: center; gap: 10px; margin-top: 12px; }
.page-actions button, .secondary, .finish { border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; color: var(--text-muted); background: transparent; cursor: pointer; }
.page-actions button:disabled, button:disabled { cursor: default; opacity: .4; }
.page-actions span { color: var(--text-faint); text-align: center; font-size: 10px; }
.note-box { display: block; margin-top: 17px; }
.note-box > span { color: var(--text-muted); font-size: 11px; }
.note-box textarea { box-sizing: border-box; width: 100%; margin-top: 7px; padding: 12px 14px; resize: vertical; border: 1px solid var(--border); border-radius: 13px; outline: none; color: var(--text); background: color-mix(in srgb, var(--bg-card) 82%, transparent); font: inherit; font-size: 12px; line-height: 1.6; }
.note-box textarea:focus { border-color: var(--accent); }
.notice-line { min-height: 34px; display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.notice-line > span { color: var(--accent); font-size: 10px; }
.notice-line > span.error, .empty.error { color: #df7d86; }
.primary-actions { display: grid; grid-template-columns: 1fr auto; gap: 10px; margin-top: 8px; }
.talk { border: 0; border-radius: 12px; padding: 11px 16px; color: var(--bg-main); background: var(--accent); font-weight: 600; cursor: pointer; }
.finish { padding-inline: 18px; }
.completed-card { margin-top: 18px; padding: 18px; display: flex; align-items: center; gap: 13px; border: 1px solid var(--border); border-radius: 16px; background: color-mix(in srgb, var(--accent) 8%, var(--bg-card)); }
.completed-card > span { color: var(--accent); font-size: 24px; }
.completed-card div { flex: 1; }
.completed-card p { margin: 3px 0 0; color: var(--text-muted); font-size: 11px; }
.completed-card button { border: 0; color: var(--accent); background: transparent; cursor: pointer; }
.viewpoints { margin-top: 17px; padding: 13px; display: grid; gap: 9px; border: 1px dashed var(--border); border-radius: 14px; }
.vp-title { color: var(--text-muted); font-size: 11px; }
.viewpoints label { display: grid; gap: 5px; }
.viewpoints label > span { color: var(--text-faint); font-size: 10px; }
.viewpoints textarea { box-sizing: border-box; width: 100%; padding: 9px 12px; resize: vertical; border: 1px solid var(--border); border-radius: 11px; outline: none; color: var(--text); background: color-mix(in srgb, var(--bg-card) 82%, transparent); font: inherit; font-size: 12px; line-height: 1.6; }
.viewpoints textarea:focus { border-color: var(--accent); }
.viewpoints .secondary { justify-self: end; }
.summary-box { margin-top: 14px; padding: 15px 17px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 86%, transparent); }
.summary-box > span { color: var(--accent); font-size: 10px; letter-spacing: .14em; }
.summary-box p { margin: 8px 0 0; white-space: pre-wrap; color: var(--text); font-size: 12px; line-height: 1.8; }
.shelf-section { margin-bottom: 22px; }
.section-title h3 { margin: 3px 0 10px; font-size: 15px; font-weight: 550; }
.activity-list { display: grid; gap: 7px; }
.activity-list button, .history-row { padding: 11px 13px; display: flex; align-items: center; gap: 11px; border: 1px solid var(--border); border-radius: 13px; color: var(--text); background: color-mix(in srgb, var(--bg-card) 84%, transparent); text-align: left; cursor: pointer; width: 100%; }
.activity-list .format { width: 36px; padding: 4px 0; flex-shrink: 0; border-radius: 7px; color: var(--accent); background: var(--bg-hover); text-align: center; font-size: 9px; text-transform: uppercase; }
.activity-list button > span:nth-child(2) { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 2px; }
.activity-list strong { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 12px; font-weight: 500; }
.activity-list small, .activity-list em { color: var(--text-muted); font-size: 10px; font-style: normal; }
.document-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }
.document-grid button { min-width: 0; min-height: 100px; padding: 13px; display: flex; flex-direction: column; align-items: flex-start; gap: 7px; border: 1px solid var(--border); border-radius: 14px; color: var(--text); background: color-mix(in srgb, var(--bg-card) 86%, transparent); text-align: left; cursor: pointer; }
.document-grid button:hover { border-color: var(--accent); }
.document-grid span { color: var(--accent); font-size: 9px; text-transform: uppercase; }
.document-grid strong { width: 100%; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 12px; font-weight: 550; }
.document-grid small { color: var(--text-muted); font-size: 10px; }
.open-bookshelf { display: block; margin: 0 auto; border: 0; border-radius: 11px; padding: 9px 15px; color: var(--bg-main); background: var(--accent); cursor: pointer; }
.history-row { padding: 7px 11px; margin-bottom: 6px; font-size: 11px; }
.history-row:hover { border-color: var(--accent); }
.history-row > span { color: #74b997; }
.history-row strong { flex: 1; color: var(--text); font-weight: 500; }
.empty { padding: 60px 0; color: var(--text-muted); text-align: center; }
.empty.small { padding: 25px 0 14px; font-size: 12px; }
@media (max-width: 620px) {
  .activity-panel { padding: 20px 16px; }
  .document-grid { grid-template-columns: 1fr 1fr; }
  .excerpt { padding: 24px 22px; }
  .page-actions { grid-template-columns: 80px 1fr 80px; }
  .primary-actions { grid-template-columns: 1fr; }
  .goal-progress-row { grid-template-columns: 1fr; }
}
</style>
