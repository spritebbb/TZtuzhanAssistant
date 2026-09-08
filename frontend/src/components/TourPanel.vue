<script setup lang="ts">
import { ref, watch } from 'vue'
import { getTour, type TourScript } from '../api/tour'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'use', text: string): void }>()

const tour = ref<TourScript | null>(null)
const loading = ref(false)
const error = ref('')
const copied = ref('')

async function load() {
  if (tour.value) return
  loading.value = true
  error.value = ''
  try {
    tour.value = await getTour()
  } catch {
    error.value = '演示脚本暂时读不到，过会儿再试'
  } finally {
    loading.value = false
  }
}

function useStep(text: string) {
  emit('use', text)
  emit('close')
}

function markCopied(id: string) {
  copied.value = id
  window.setTimeout(() => { if (copied.value === id) copied.value = '' }, 1500)
}

watch(() => props.show, (show) => { if (show) void load() })
</script>

<template>
  <div v-if="show" class="tour-mask" @click.self="emit('close')">
    <section class="tour-panel" role="dialog" aria-modal="true" :aria-label="tour?.title || '能力演示'">
      <header>
        <div>
          <span class="eyebrow">AGENT TOUR</span>
          <h2>{{ tour?.title || '能力演示' }}</h2>
        </div>
        <button class="close" title="关闭" @click="emit('close')">×</button>
      </header>
      <p class="intro">{{ tour?.intro || '想看我会什么？一步步做给你看。' }}</p>
      <p class="hint">点「发这句」会把原话填进输入框，按 Enter 发送即可。每步都会真实调用工具。</p>

      <p v-if="loading" class="empty">正在准备演示…</p>
      <p v-else-if="error" class="empty" role="alert">{{ error }}</p>
      <ol v-else class="steps">
        <li v-for="(step, index) in tour?.steps || []" :key="step.id" class="step">
          <div class="step-head">
            <span class="step-no">{{ index + 1 }}</span>
            <div>
              <h3>{{ step.title }}</h3>
              <p class="shows">{{ step.shows }}</p>
            </div>
          </div>
          <blockquote class="prompt">{{ step.prompt }}</blockquote>
          <div class="step-actions">
            <button class="use" @click="useStep(step.prompt)">发这句</button>
            <button class="ghost" @click="markCopied(step.id)">
              {{ copied === step.id ? '已复制' : '复制' }}
            </button>
          </div>
          <p class="meta">
            <span class="tools">{{ step.tools.join(' · ') }}</span>
            <span class="check">验收：{{ step.check }}</span>
          </p>
          <p v-if="step.follow_up" class="follow">跟一句：{{ step.follow_up }}</p>
        </li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.tour-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8, 10, 16, .58); backdrop-filter: blur(5px); }
.tour-panel { width: min(560px, 94vw); height: 100%; padding: 26px 24px; overflow-y: auto; color: var(--text); background: linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -20px 0 55px rgba(0,0,0,.25); }
header { display: flex; align-items: flex-start; justify-content: space-between; }
.eyebrow { color: var(--accent); font-size: 10px; letter-spacing: .19em; }
h2 { margin: 5px 0 12px; font-size: 24px; font-weight: 600; }
.close { border: 0; color: var(--text-muted); background: transparent; font-size: 28px; cursor: pointer; }
.intro { margin: 0 0 6px; line-height: 1.7; }
.hint { margin: 0 0 16px; color: var(--text-muted); font-size: 13px; line-height: 1.7; }
.steps { margin: 0; padding: 0; list-style: none; }
.step { margin-bottom: 14px; padding: 14px 15px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
.step-head { display: flex; gap: 10px; align-items: flex-start; }
.step-no { flex: 0 0 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; background: var(--accent); color: #fff; font-size: 13px; }
h3 { margin: 2px 0 2px; font-size: 16px; }
.shows { margin: 0; color: var(--text-muted); font-size: 13px; }
.prompt { margin: 10px 0; padding: 10px 12px; border-left: 3px solid var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); border-radius: 0 10px 10px 0; line-height: 1.7; }
.step-actions { display: flex; gap: 8px; }
button.use, button.ghost { padding: 6px 14px; border-radius: 9px; border: 1px solid var(--accent); background: var(--accent); color: #fff; cursor: pointer; font-size: 13px; }
button.ghost { background: transparent; color: var(--accent); }
.meta { display: flex; flex-direction: column; gap: 2px; margin: 10px 0 0; font-size: 12px; color: var(--text-muted); }
.tools { color: var(--accent); }
.follow { margin: 8px 0 0; font-size: 13px; color: var(--text-muted); }
.empty { color: var(--text-muted); text-align: center; padding: 40px 12px; }
@media (max-width: 480px) {
  .tour-panel { padding: 20px 16px; }
}
</style>
