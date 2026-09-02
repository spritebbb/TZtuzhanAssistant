<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiFetch } from '../api'
import { listArchives, getArchive, type ArchiveInfo, type Message } from '../api/sessions'

const emit = defineEmits<{
  (e: 'close-sidebar'): void
  (e: 'open-settings'): void
}>()

const props = defineProps<{
  open: boolean
  currentId: string | null
}>()

const archives = ref<ArchiveInfo[]>([])
const viewing = ref<ArchiveInfo | null>(null)
const viewingMessages = ref<Message[]>([])
// 菟菚心情（来自 /api/meta，真实数据而非写死）
const mood = ref({ value: 60, label: '平淡', emoji: '🌱' })

async function load() {
  archives.value = await listArchives()
  try {
    const r = await apiFetch('/api/meta')
    const d = await r.json()
    if (d.mood) mood.value = d.mood
  } catch { /* 心情加载失败时保留默认值 */ }
}

async function viewArchive(a: ArchiveInfo) {
  viewing.value = a
  const detail = await getArchive(a.id)
  viewingMessages.value = detail?.messages ?? []
}

function closeView() {
  viewing.value = null
  viewingMessages.value = []
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(load)

defineExpose({ load })
</script>

<template>
  <aside class="sidebar" :class="{ open }">
    <div class="brand">
      <!-- 菟丝藤 logo -->
      <svg viewBox="0 0 40 40" class="brand-logo" width="30" height="30">
        <path d="M8 32 Q 14 22, 22 24 T 30 12" fill="none" stroke="#a4b85c" stroke-width="2.4" stroke-linecap="round"/>
        <path d="M14 22 Q 20 14, 18 6" fill="none" stroke="#c6d680" stroke-width="1.8" stroke-linecap="round"/>
        <circle cx="19" cy="4" r="3.2" fill="#f6e7c8"/>
        <circle cx="19" cy="4" r="1.8" fill="#d9a860"/>
        <circle cx="30" cy="11" r="2.6" fill="#f6e7c8"/>
        <circle cx="30" cy="11" r="1.5" fill="#d9a860"/>
      </svg>
      <span class="brand-name">菟菚</span>
      <span class="brand-sub">归档</span>
    </div>

    <div class="list-header">
      <span class="list-title">历史归档</span>
      <span class="list-hint">会话结束后自动归档</span>
    </div>

    <div class="slist">
      <div v-if="!archives.length" class="sitem empty">（还没有归档记录）</div>
      <div
        v-for="a in archives"
        :key="a.id"
        class="sitem"
        :class="{ active: viewing?.id === a.id }"
        @click="viewArchive(a)"
      >
        <span class="t">{{ a.title }}</span>
        <span class="meta">{{ a.message_count }} 条</span>
      </div>
    </div>

    <div class="moodcard">
      <div class="moodemoji">{{ mood.emoji }}</div>
      <div class="moodinfo">
        <div class="moodlabel">心情 · 菟菚（{{ mood.label }}）</div>
        <div class="moodbar"><div class="moodfill" :style="{ width: mood.value + '%' }"></div></div>
      </div>
    </div>

    <div class="foot">
      <span class="foot-text">归档保存在本机 SQLite</span>
      <span class="settings-link" @click="emit('open-settings')">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3"/>
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
        </svg>
        设置
      </span>
    </div>

    <!-- 归档详情浮层 -->
    <div v-if="viewing" class="viewer-mask" @click.self="closeView">
      <div class="viewer">
        <div class="viewer-head">
          <span class="viewer-title">{{ viewing.title }}</span>
          <span class="viewer-time">{{ fmtTime(viewing.created_at) }}</span>
          <button class="viewer-close" @click="closeView">✕</button>
        </div>
        <div class="viewer-body">
          <div v-if="!viewingMessages.length" class="viewer-empty">（无内容）</div>
          <div v-for="(m, i) in viewingMessages" :key="i" class="vmsg" :class="m.role">
            <div class="vwho">{{ m.role === 'user' ? '你' : '菟菚' }}</div>
            <div class="vcontent">{{ m.content || '（图片）' }}</div>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 248px;
  flex-shrink: 0;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: relative;
}
.brand {
  padding: 16px 16px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.brand-logo {
  flex-shrink: 0;
  filter: drop-shadow(0 2px 4px rgba(164, 184, 92, 0.3));
}
.brand-name {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.5px;
}
.brand-sub {
  font-size: 0.68rem;
  color: var(--text-faint);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius-full);
  padding: 2px 8px;
  margin-left: 2px;
}
.list-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 16px 8px;
}
.list-title {
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--text-dim);
}
.list-hint {
  font-size: 0.66rem;
  color: var(--text-faint);
}

.slist {
  flex: 1;
  overflow-y: auto;
  padding: 2px 8px 10px;
}
.sitem {
  padding: 10px 12px;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 0.83rem;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
  transition: all 0.15s ease;
}
.sitem:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.sitem.active {
  background: linear-gradient(135deg, var(--primary-soft), #e8efc9);
  color: var(--primary-deep);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px rgba(164, 184, 92, 0.35);
}
.sitem.empty { cursor: default; color: var(--text-faint); }
.sitem .t {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sitem .meta {
  font-size: 0.68rem;
  color: var(--text-faint);
  flex-shrink: 0;
}

.moodcard {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin: 6px 10px 2px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}
.moodemoji { font-size: 1.5rem; line-height: 1; }
.moodinfo { flex: 1; min-width: 0; }
.moodlabel { font-size: 0.72rem; color: var(--text-dim); margin-bottom: 4px; font-weight: 600; }
.moodbar { height: 5px; background: var(--bg-hover); border-radius: var(--radius-full); overflow: hidden; }
.moodfill {
  height: 100%;
  border-radius: var(--radius-full);
  transition: width 0.6s ease;
  background: linear-gradient(90deg, var(--primary), var(--accent));
}

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  font-size: 0.7rem;
  color: var(--text-faint);
  border-top: 1px solid var(--border-light);
}
.settings-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--primary-deep);
  cursor: pointer;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  transition: all 0.15s ease;
}
.settings-link:hover { background: var(--primary-soft); }

/* 归档详情浮层 */
.viewer-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.28);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
}
.viewer {
  width: min(640px, 86vw);
  max-height: 78vh;
  background: var(--bg);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.viewer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.viewer-title {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.viewer-time {
  font-size: 0.7rem;
  color: var(--text-faint);
  flex-shrink: 0;
}
.viewer-close {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border: none;
  background: var(--bg-hover);
  color: var(--text-dim);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.8rem;
}
.viewer-close:hover { color: var(--danger); background: var(--danger-soft); }
.viewer-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.viewer-empty { color: var(--text-faint); font-size: 0.8rem; text-align: center; padding: 20px 0; }
.vmsg { display: flex; flex-direction: column; gap: 3px; }
.vmsg.user { align-items: flex-end; }
.vmsg.bot { align-items: flex-start; }
.vwho { font-size: 0.66rem; color: var(--text-faint); }
.vcontent {
  max-width: 82%;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  font-size: 0.83rem;
  line-height: 1.5;
  word-break: break-word;
  white-space: pre-wrap;
}
.vmsg.user .vcontent { background: var(--bg-user); color: #fff; }
.vmsg.bot .vcontent { background: #fff; border: 1px solid var(--border); color: var(--text); }

@media (max-width: 720px) {
  .sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: 260px;
    z-index: 60;
    transform: translateX(-105%);
    transition: transform 0.28s ease;
    box-shadow: var(--shadow-lg);
  }
  .sidebar.open { transform: translateX(0); }
}
</style>
