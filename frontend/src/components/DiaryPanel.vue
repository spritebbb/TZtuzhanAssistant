<script setup lang="ts">
import { ref, watch } from 'vue'
import { getDiaries, getResearchReports, type DiaryEntry, type ResearchReport } from '../api/diary'
import { getUnlocks, type UnlockSlot } from '../api/unlocks'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const tab = ref<'diary' | 'research' | 'ours'>('diary')
const diaries = ref<DiaryEntry[]>([])
const reports = ref<ResearchReport[]>([])
const unlocks = ref<UnlockSlot[]>([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [diaryRows, reportRows, slotRows] = await Promise.all([getDiaries(), getResearchReports(), getUnlocks()])
    diaries.value = diaryRows
    reports.value = reportRows
    unlocks.value = slotRows
  } catch {
    error.value = '抽屉卡住了，过会儿再翻'
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (show) => { if (show) void load() })
</script>

<template>
  <div v-if="show" class="diary-mask" @click.self="emit('close')">
    <section class="diary-panel" role="dialog" aria-modal="true" aria-label="菟菚的私人记录">
      <header>
        <div>
          <span class="eyebrow">PRIVATE FIELD NOTES</span>
          <h2>菟菚的抽屉</h2>
        </div>
        <button class="close" title="关闭" @click="emit('close')">×</button>
      </header>
      <nav>
        <button :class="{ active: tab === 'diary' }" @click="tab = 'diary'">私人日记</button>
        <button :class="{ active: tab === 'research' }" @click="tab = 'research'">观察人类</button>
        <button :class="{ active: tab === 'ours' }" @click="tab = 'ours'">我们之间</button>
      </nav>
      <div class="entries">
        <p v-if="loading" class="empty">正在悄悄拉开抽屉…</p>
        <p v-else-if="error" class="empty">{{ error }}</p>
        <template v-else-if="tab === 'diary'">
          <article v-for="entry in diaries" :key="entry.id">
            <div class="meta"><time>{{ entry.date }}</time><span v-if="entry.mood">{{ entry.mood }}</span></div>
            <p>{{ entry.content }}</p>
          </article>
          <p v-if="!diaries.length" class="empty">还没有日记。聊过一个完整的日子后再来偷看</p>
        </template>
        <template v-else-if="tab === 'research'">
          <article v-for="report in reports" :key="report.id" class="report">
            <div class="meta"><time>{{ report.period }}</time><span>阶段报告</span></div>
            <h3>{{ report.title }}</h3>
            <p>{{ report.content }}</p>
          </article>
          <p v-if="!reports.length" class="empty">样本还不够。每积累七篇日记，她会写一份阶段报告</p>
        </template>
        <template v-else>
          <article v-for="slot in unlocks" :key="slot.key" :class="['unlock', slot.status]">
            <div class="meta">
              <time v-if="slot.delivered_at">{{ slot.delivered_at.slice(0, 10) }}</time>
              <time v-else>——</time>
              <span>{{ slot.kind === 'stage' ? '阶段' : slot.kind === 'bond' ? '羁绊' : '彩蛋' }}</span>
            </div>
            <h3>{{ slot.status === 'locked' ? '？？？' : slot.title }}</h3>
            <p v-if="slot.status === 'delivered' && slot.content">{{ slot.content }}</p>
            <p v-else-if="slot.status === 'pending'" class="hint">她有句话想对你说，下次聊天时留意</p>
            <p v-else-if="slot.status === 'locked'" class="hint">还没走到这一页</p>
          </article>
          <p v-if="!unlocks.length" class="empty">日子还长，慢慢来</p>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.diary-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8, 10, 16, .58); backdrop-filter: blur(5px); }
.diary-panel { width: min(540px, 94vw); height: 100%; padding: 26px 24px; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -20px 0 55px rgba(0,0,0,.25); }
header { display: flex; align-items: flex-start; justify-content: space-between; }
.eyebrow { color: var(--accent); font-size: 10px; letter-spacing: .19em; }
h2 { margin: 5px 0 18px; font-size: 24px; font-weight: 600; }
.close { border: 0; color: var(--text-muted); background: transparent; font-size: 28px; cursor: pointer; }
nav { display: flex; gap: 8px; border-bottom: 1px solid var(--border); }
nav button { padding: 9px 12px; border: 0; border-bottom: 2px solid transparent; color: var(--text-muted); background: transparent; cursor: pointer; }
nav button.active { color: var(--accent); border-bottom-color: var(--accent); }
.entries { overflow-y: auto; padding: 18px 2px 50px; }
article { margin-bottom: 14px; padding: 16px 17px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
.meta { display: flex; justify-content: space-between; color: var(--text-muted); font-size: 12px; }
.meta span { color: var(--accent); }
h3 { margin: 9px 0 3px; font-size: 16px; }
p { margin: 10px 0 0; line-height: 1.78; white-space: pre-wrap; }
.empty { color: var(--text-muted); text-align: center; padding: 44px 12px; }
article.unlock.locked { opacity: .55; border-style: dashed; }
article.unlock.pending { border-color: var(--accent); }
article.unlock .hint { color: var(--text-muted); font-size: 13px; }
</style>
