<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  deleteKnowledgeDocument,
  extractKnowledgeOpinions,
  listKnowledgeDocuments,
  listKnowledgeOpinions,
  revokeKnowledgeOpinion,
  uploadKnowledgeDocument,
  type KnowledgeDocument,
  type KnowledgeOpinion,
} from '../api/knowledge'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const documents = ref<KnowledgeDocument[]>([])
const loading = ref(false)
const uploading = ref(false)
const error = ref('')
const notice = ref('')
const dragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const opinions = ref<KnowledgeOpinion[]>([])
const opinionsLoading = ref(false)
const extracting = ref(false)
const extractNote = ref('')

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)}MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)}KB`
  return `${bytes}B`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    documents.value = await listKnowledgeDocuments()
    opinions.value = await listKnowledgeOpinions()
  } catch {
    error.value = '书架暂时打不开，过会儿再看'
  } finally {
    loading.value = false
  }
}

async function extract(doc: KnowledgeDocument) {
  extracting.value = true
  extractNote.value = ''
  error.value = ''
  try {
    const result = await extractKnowledgeOpinions(doc.id)
    if (!result.ok) {
      error.value = result.error || '她还没读出什么观点，过会儿再试'
      return
    }
    if (result.opinions?.length) {
      extractNote.value = `读完《${doc.filename}》，她有了新的想法`
      opinions.value = [...result.opinions, ...opinions.value]
    } else {
      extractNote.value = result.note || '她还没读出什么想说的，过会儿再试试'
    }
  } catch {
    error.value = '提取失败，过会儿再试'
  } finally {
    extracting.value = false
  }
}

async function revoke(opinion: KnowledgeOpinion) {
  const ok = await revokeKnowledgeOpinion(opinion.id)
  if (!ok) {
    error.value = '撤销失败，过会儿再试'
    return
  }
  opinions.value = opinions.value.filter((o) => o.id !== opinion.id)
}

async function upload(file: File) {
  uploading.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await uploadKnowledgeDocument(file)
    if (!result.ok) {
      error.value = result.error || '这份她读不进去'
      return
    }
    notice.value = `《${result.document?.filename}》读完了，记成 ${result.document?.chunk_count} 段`
    await load()
  } catch {
    error.value = '上传失败，过会儿再试'
  } finally {
    uploading.value = false
  }
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void upload(file)
  input.value = ''
}

function onDrop(event: DragEvent) {
  dragOver.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) void upload(file)
}

async function remove(doc: KnowledgeDocument) {
  error.value = ''
  const ok = await deleteKnowledgeDocument(doc.id)
  if (!ok) {
    error.value = '删除失败，过会儿再试'
    return
  }
  documents.value = documents.value.filter((d) => d.id !== doc.id)
}

watch(() => props.show, (show) => { if (show) void load() })
</script>

<template>
  <div v-if="show" class="kb-mask" @click.self="emit('close')">
    <section class="kb-panel" role="dialog" aria-modal="true" :aria-label="(props.personaName || '助手') + '的书架'">
      <header>
        <div>
          <span class="eyebrow">BOOKSHELF</span>
          <h2>{{ props.personaName || '助手' }}的书架</h2>
        </div>
        <button class="close" title="关闭" @click="emit('close')">×</button>
      </header>
      <p class="hint">投喂 pdf / txt / md，她真的会读，聊到相关话题会自然提起</p>

      <div
        class="dropzone"
        :class="{ over: dragOver, busy: uploading }"
        @click="fileInput?.click()"
        @dragover.prevent="dragOver = true"
        @dragleave="dragOver = false"
        @drop.prevent="onDrop"
      >
        <input
          ref="fileInput"
          type="file"
          accept=".pdf,.txt,.md,.epub"
          hidden
          @change="onPick"
        />
        <span v-if="uploading">她正在读，稍等…</span>
        <span v-else>点这里选文件，或直接把文件拖进来</span>
      </div>

      <p v-if="notice" class="notice">{{ notice }}</p>
      <p v-if="error" class="error-text">{{ error }}</p>

      <div class="entries">
        <p v-if="loading" class="empty">正在整理书架…</p>
        <p v-else-if="!documents.length" class="empty">书架还空着，投喂一份试试</p>
        <div v-for="doc in documents" :key="doc.id" class="doc-row">
          <span class="doc-format">{{ doc.format }}</span>
          <div class="doc-meta">
            <strong :title="doc.filename">{{ doc.filename }}</strong>
            <span class="sub">{{ doc.chunk_count }} 段 · {{ fmtSize(doc.size_bytes) }} · {{ doc.ts.slice(0, 10) }}</span>
          </div>
          <button class="delete" :disabled="extracting" title="让她读出观点" @click="extract(doc)">读出观点</button>
          <button class="delete" title="从书架上拿掉" @click="remove(doc)">拿掉</button>
        </div>
      </div>

      <div v-if="documents.length" class="opinion-block">
        <div class="opinion-head">
          <span class="eyebrow">HER OPINIONS</span>
          <span class="opinion-title">她形成的观点</span>
        </div>
        <p class="hint">聊到相关话题时她会自然提起；你可以撤销不想要的</p>
        <p v-if="extractNote" class="notice">{{ extractNote }}</p>
        <p v-if="opinionsLoading" class="empty">正在回想…</p>
        <p v-else-if="!opinions.length" class="empty">还没有——点「读出观点」让她先消化一下</p>
        <div v-for="o in opinions" :key="o.id" class="opinion-row">
          <div class="opinion-meta">
            <strong>{{ o.stance }}</strong>
            <span class="sub">来自《{{ o.filename }}》 · 依据 {{ o.source_spans.length }} 段原文</span>
          </div>
          <button class="delete" title="撤销这条观点" @click="revoke(o)">撤销</button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.kb-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8, 10, 16, .58); backdrop-filter: blur(5px); }
.kb-panel { width: min(480px, 94vw); height: 100%; padding: 26px 24px; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -20px 0 55px rgba(0,0,0,.25); }
header { display: flex; align-items: flex-start; justify-content: space-between; }
.eyebrow { color: var(--accent); font-size: 10px; letter-spacing: .19em; }
h2 { margin: 5px 0 8px; font-size: 24px; font-weight: 600; }
.close { border: 0; color: var(--text-muted); background: transparent; font-size: 28px; cursor: pointer; }
.hint { margin: 0 0 14px; color: var(--text-muted); font-size: 12px; }
.dropzone { display: flex; align-items: center; justify-content: center; min-height: 84px; border: 1.5px dashed var(--border); border-radius: 14px; color: var(--text-muted); font-size: 13px; cursor: pointer; transition: border-color .2s, background .2s; }
.dropzone:hover, .dropzone.over { border-color: var(--accent); color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
.dropzone.busy { pointer-events: none; opacity: .7; }
.notice { margin: 10px 0 0; color: var(--accent); font-size: 12px; }
.error-text { margin: 10px 0 0; color: #e07070; font-size: 12px; }
.entries { margin-top: 16px; overflow-y: auto; padding-bottom: 40px; }
.doc-row { display: flex; align-items: center; gap: 12px; padding: 12px 14px; margin-bottom: 8px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
.doc-format { flex-shrink: 0; width: 40px; text-align: center; padding: 4px 0; border-radius: 8px; background: var(--bg-hover); color: var(--accent); font-size: 11px; text-transform: uppercase; }
.doc-meta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.doc-meta strong { font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.doc-meta .sub { font-size: 11px; color: var(--text-muted); }
.delete { flex-shrink: 0; border: 1px solid var(--border); border-radius: 8px; padding: 4px 10px; color: var(--text-muted); background: transparent; font-size: 12px; cursor: pointer; }
.delete:hover { color: #e07070; border-color: #e07070; }
.empty { color: var(--text-muted); text-align: center; padding: 40px 0; }
.opinion-block { margin-top: 18px; }
.opinion-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
.opinion-title { font-size: 14px; font-weight: 600; }
.opinion-row { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; margin-bottom: 8px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--accent) 5%, transparent); }
.opinion-meta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.opinion-meta strong { font-size: 13px; font-weight: 500; line-height: 1.5; }
.opinion-meta .sub { font-size: 11px; color: var(--text-muted); }
</style>
