<script setup lang="ts">
import { ref, watch } from 'vue'
import { listArtifacts, type ArtifactItem } from '../api/artifacts'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const artifacts = ref<ArtifactItem[]>([])
const loading = ref(false)
const error = ref('')

const TYPE_LABELS: Record<string, string> = {
  book_summary: '共同书摘',
  goal_review: '目标回顾',
  co_story: '共同故事',
}

function typeLabel(type: string) {
  return TYPE_LABELS[type] || '共同回忆'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    artifacts.value = await listArtifacts()
  } catch {
    error.value = '角落暂时打不开，过会儿再来'
  } finally {
    loading.value = false
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
        <template v-else-if="artifacts.length">
          <article v-for="item in artifacts" :key="item.id" class="artifact-card">
            <div class="card-head">
              <span class="type">{{ typeLabel(item.artifact_type) }}</span>
              <time>{{ item.updated_at.slice(0, 10) }}</time>
            </div>
            <h3>{{ item.title }}</h3>
            <p>{{ item.content }}</p>
            <small v-if="item.version > 1" class="version">已更新 {{ item.version }} 版</small>
          </article>
        </template>
        <p v-else class="empty">
          这里还空着——和{{ props.personaName || '她' }}一起读完第一本书或完成第一个目标，就会留下第一件东西
        </p>
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
.artifact-card { margin-bottom: 12px; padding: 14px 16px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
.card-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.type { padding: 2px 8px; border-radius: 99px; color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); font-size: 10px; }
time { color: var(--text-muted); font-size: 10px; }
.artifact-card h3 { margin: 0 0 8px; font-size: 14px; font-weight: 550; }
.artifact-card p { margin: 0; white-space: pre-wrap; color: var(--text); font-size: 12px; line-height: 1.8; }
.version { display: block; margin-top: 8px; color: var(--text-muted); font-size: 10px; }
.empty { color: var(--text-muted); text-align: center; padding: 60px 0; }
</style>
