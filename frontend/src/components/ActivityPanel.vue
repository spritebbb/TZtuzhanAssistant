<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  completeReading,
  listReadingActivities,
  resumeReading,
  saveReadingNote,
  setReadingPosition,
  startReading,
  type ReadingActivity,
} from '../api/activities'
import { listKnowledgeDocuments, type KnowledgeDocument } from '../api/knowledge'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-bookshelf'): void
  (e: 'discuss', draft: string): void
}>()

const activities = ref<ReadingActivity[]>([])
const documents = ref<KnowledgeDocument[]>([])
const current = ref<ReadingActivity | null>(null)
const noteDraft = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')

const unfinished = computed(() => activities.value.filter(item => item.status !== 'completed'))
const completed = computed(() => activities.value.filter(item => item.status === 'completed').slice(0, 4))

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
    const [activityRows, documentRows] = await Promise.all([
      listReadingActivities(),
      listKnowledgeDocuments(),
    ])
    activities.value = activityRows
    documents.value = documentRows
    const active = activityRows.find(item => item.status === 'active') ?? null
    current.value = active
    noteDraft.value = active?.note ?? ''
  } catch {
    error.value = '共读角落暂时打不开，过会儿再来'
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
    applyCurrent(await action())
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

async function discuss() {
  if (!current.value || busy.value) return
  if (noteDraft.value.trim() !== current.value.note) {
    await run(() => saveReadingNote(current.value!.id, noteDraft.value))
    if (error.value) return
  }
  const item = current.value
  emit(
    'discuss',
    `我们继续共读《${item.filename}》吧。现在这一段，你怎么看？`,
  )
}

function finish() {
  if (!current.value) return
  void run(() => completeReading(current.value!.id), '这本书读完了，但话还没聊完')
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
          <p>先从共读开始，把话题从聊天框里拿出来</p>
        </div>
        <button class="close" title="关闭" @click="emit('close')">×</button>
      </header>

      <div class="body">
        <p v-if="loading" class="empty">正在找上次夹的书签…</p>
        <p v-else-if="error && !current" class="empty error">{{ error }}</p>

        <template v-else-if="current">
          <button class="back" @click="leaveCurrent">← 共读书架</button>
          <div class="reading-head">
            <div>
              <span class="status" :class="current.status">{{ current.status === 'completed' ? '已读完' : '共读中' }}</span>
              <h3>{{ current.filename }}</h3>
              <p>第 {{ current.position + 1 }} / {{ current.total }} 段 · 已留 {{ current.note_count }} 张书签</p>
            </div>
            <strong>{{ current.progress }}%</strong>
          </div>
          <div class="progress"><span :style="{ width: current.progress + '%' }"></span></div>

          <article class="excerpt">
            <span class="quote-mark">“</span>
            <p>{{ current.excerpt || '这一段暂时没有可读文字' }}</p>
          </article>

          <template v-if="current.status !== 'completed'">
            <div class="page-actions">
              <button :disabled="busy || current.position === 0" @click="move(-1)">上一段</button>
              <span>慢慢读，不赶进度</span>
              <button :disabled="busy || current.position >= current.total - 1" @click="move(1)">下一段</button>
            </div>

            <label class="note-box">
              <span>这一段的书签</span>
              <textarea v-model="noteDraft" rows="3" maxlength="2000" placeholder="写下一句想法，下次回来还在这里"></textarea>
            </label>
            <div class="notice-line">
              <span :class="{ error: !!error }">{{ error || notice }}</span>
              <button class="secondary" :disabled="busy" @click="saveNote">收进书签</button>
            </div>

            <div class="primary-actions">
              <button class="talk" :disabled="busy" @click="discuss">去和{{ props.personaName || '助手' }}聊这一段</button>
              <button class="finish" :disabled="busy" @click="finish">这本读完了</button>
            </div>
          </template>
          <div v-else class="completed-card">
            <span>✶</span>
            <div><strong>一起读到了最后</strong><p>{{ notice || '这些书签会留在这里' }}</p></div>
            <button @click="begin(current.document_id)">再读一遍</button>
          </div>
        </template>

        <template v-else>
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
            <div class="section-title"><span>FINISHED</span><h3>一起读过</h3></div>
            <div v-for="item in completed" :key="item.id" class="history-row">
              <span>✓</span><strong>{{ item.filename }}</strong><small>{{ item.note_count }} 张书签</small>
            </div>
          </section>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.activity-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8,10,16,.64); backdrop-filter: blur(6px); }
.activity-panel { width: min(720px, 97vw); height: 100%; padding: 26px 28px; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: radial-gradient(circle at 82% 3%, color-mix(in srgb, var(--accent) 15%, transparent), transparent 34%), linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -24px 0 65px rgba(0,0,0,.3); }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; flex-shrink: 0; }
.eyebrow, .section-title > span { color: var(--accent); font-size: 10px; letter-spacing: .18em; }
h2 { margin: 5px 0 3px; font-size: 26px; font-weight: 600; }
header p, .reading-head p { margin: 0; color: var(--text-muted); font-size: 12px; }
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
.shelf-section { margin-bottom: 22px; }
.section-title h3 { margin: 3px 0 10px; font-size: 15px; font-weight: 550; }
.activity-list { display: grid; gap: 7px; }
.activity-list button { padding: 11px 13px; display: flex; align-items: center; gap: 11px; border: 1px solid var(--border); border-radius: 13px; color: var(--text); background: color-mix(in srgb, var(--bg-card) 84%, transparent); text-align: left; cursor: pointer; }
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
.history-row { padding: 7px 2px; display: flex; align-items: center; gap: 9px; color: var(--text-muted); font-size: 11px; }
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
}
</style>
