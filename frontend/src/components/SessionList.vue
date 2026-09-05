<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { apiFetch } from '../api'
import { listArchives, getArchive, searchArchives as apiSearchArchives, type ArchiveInfo, type Message, type ArchiveDetail, type ArchiveSearchResult } from '../api/sessions'
import { resolveImageSrc } from '../utils/images'

const emit = defineEmits<{
  (e: 'close-sidebar'): void
  (e: 'open-settings'): void
}>()

const props = defineProps<{
  open: boolean
  currentId: string | null
  personaName?: string
}>()

const archives = ref<ArchiveInfo[]>([])
const viewing = ref<ArchiveInfo | null>(null)
const viewingMessages = ref<Message[]>([])
const mood = ref({ value: 60, label: '平淡', emoji: '🌱' })

// === 归档搜索 ===
const searchQuery = ref('')
const searchResult = ref<ArchiveSearchResult[]>([])
const searching = ref(false)
const searchMsg = ref('')

async function searchArchives() {
  const q = searchQuery.value.trim()
  if (!q) {
    searchResult.value = []
    return
  }
  searching.value = true
  searchMsg.value = ''
  try {
    // 后端一次 LIKE 查询命中标题 + 内容，返回摘要列表（标题/条数/预览），
    // 替代「拉全量列表 + 逐个拉详情」的 N+1 模式；点进再拉完整详情。
    const details = await apiSearchArchives(q)
    searchResult.value = details
    searchMsg.value = details.length ? `找到 ${details.length} 个相关归档` : '未找到匹配结果'
  } catch {
    searchMsg.value = '搜索失败'
  } finally {
    searching.value = false
  }
}

async function viewArchiveResult(d: ArchiveSearchResult) {
  // 搜索结果只含摘要，点进时按 id 拉完整详情
  const detail = await getArchive(d.id)
  if (detail) {
    viewing.value = detail
    viewingMessages.value = detail.messages
  }
  searchResult.value = []
  searchQuery.value = ''
}

function highlightText(text: string, q: string): string {
  if (!q || !text) return escapeHtml(text || '')
  const idx = text.toLowerCase().indexOf(q.toLowerCase())
  if (idx === -1) return escapeHtml(text)
  // 先整体转义用户输入（防 XSS），再插入高亮标记
  return (
    escapeHtml(text.slice(0, idx)) +
    '⟨' + escapeHtml(text.slice(idx, idx + q.length)) + '⟩' +
    escapeHtml(text.slice(idx + q.length))
  )
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

async function load() {
  archives.value = await listArchives()
  await refreshMood()
}

async function refreshMood() {
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

onMounted(() => {
  load()
  // 设置保存后（mood_city 等会影响心情）刷新侧栏心情，避免状态灯与能力脱节
  window.addEventListener('tztuzhan:config-saved', refreshMood)
})

onUnmounted(() => {
  window.removeEventListener('tztuzhan:config-saved', refreshMood)
})

defineExpose({ load })
</script>

<template>
  <aside class="sidebar" :class="{ open }">
    <!-- 品牌头 -->
    <div class="brand">
      <div class="brand-mark">
        <svg viewBox="0 0 40 40" class="brand-logo" width="28" height="28">
          <path d="M8 32 Q 14 22, 22 24 T 30 12" fill="none" stroke="var(--primary)" stroke-width="2.2" stroke-linecap="round"/>
          <path d="M14 22 Q 20 14, 18 6" fill="none" stroke="var(--primary-light)" stroke-width="1.8" stroke-linecap="round"/>
          <circle cx="19" cy="4" r="3.2" fill="var(--accent-light)"/>
          <circle cx="19" cy="4" r="1.8" fill="var(--accent)"/>
          <circle cx="30" cy="11" r="2.6" fill="var(--accent-light)"/>
          <circle cx="30" cy="11" r="1.5" fill="var(--accent)"/>
        </svg>
      </div>
      <div class="brand-copy">
        <span class="brand-name">{{ personaName || '助手' }}</span>
        <span class="brand-caption">PERSONA COMPANION</span>
      </div>
      <span class="brand-sub">归档</span>
    </div>
    <div class="sidebar-sprig" aria-hidden="true"><span></span><i></i><span></span></div>

    <!-- 归档搜索 -->
    <div class="search-box">
      <input
        v-model="searchQuery"
        type="text"
        placeholder="搜索归档…"
        class="search-input"
        @keyup.enter="searchArchives"
      />
      <button class="search-btn" :disabled="searching || !searchQuery.trim()" @click="searchArchives">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
      </button>
    </div>

    <!-- 搜索结果 -->
    <div v-if="searchResult.length > 0" class="slist">
      <div class="list-header">
        <span class="list-title">搜索结果</span>
        <span class="list-hint">{{ searchMsg }}</span>
      </div>
      <div
        v-for="d in searchResult"
        :key="d.id"
        class="sitem"
        @click="viewArchiveResult(d)"
      >
        <span class="t" v-html="highlightText(d.title, searchQuery)"></span>
        <span class="meta">{{ d.message_count }} 条</span>
        <span v-if="d.preview" class="sitem-preview">{{ d.preview }}</span>
      </div>
    </div>

    <!-- 归档列表 -->
    <template v-else>
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
    </template>

    <div v-if="searchMsg && !searchResult.length" class="search-note">{{ searchMsg }}</div>

    <!-- 心情卡片 -->
    <div class="moodcard glass" :title="'心情数值：' + mood.value + ' / 100'">
      <div class="moodnum">{{ mood.value }}</div>
      <div class="moodinfo">
        <div class="moodlabel">心情 · {{ mood.label }}</div>
        <div class="moodbar"><div class="moodfill" :style="{ width: mood.value + '%' }"></div></div>
      </div>
    </div>

    <!-- 底部 -->
    <div class="foot">
      <span class="foot-text">归档 · 本机 SQLite</span>
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
      <div class="viewer glass">
        <div class="viewer-head">
          <span class="viewer-title">{{ viewing.title }}</span>
          <span class="viewer-time">{{ fmtTime(viewing.created_at) }}</span>
          <a class="viewer-export" :href="`/api/keepsake/${viewing.id}`" target="_blank" rel="noopener" title="导出纪念册（可打印为 PDF）">纪念册</a>
          <button class="viewer-close" @click="closeView">✕</button>
        </div>
        <div class="viewer-body">
          <div v-if="!viewingMessages.length" class="viewer-empty">（无内容）</div>
          <div v-for="(m, i) in viewingMessages" :key="i" class="vmsg" :class="m.role">
            <div class="vwho">{{ m.role === 'user' ? '你' : (personaName || '助手') }}</div>
            <div class="vcontent">{{ m.content || '（图片）' }}</div>
            <img v-if="m.image" class="vimg" :src="resolveImageSrc(m.image)" :alt="m.content || '图片'" />
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 260px;
  flex-shrink: 0;
  /* 由主题变量驱动，避免日间模式沿用夜间靛紫顶部。 */
  background: linear-gradient(180deg, var(--bg-lg-sidebar), var(--bg-sidebar));
  backdrop-filter: blur(20px) saturate(1.1);
  -webkit-backdrop-filter: blur(20px) saturate(1.1);
  border-right: 1px solid var(--edge-subtle);
  box-shadow: inset -1px 0 0 rgba(255, 255, 255, 0.025), 16px 0 36px rgba(8, 7, 15, 0.06);
  display: flex;
  flex-direction: column;
  position: relative;
  z-index: 5;
  overflow: hidden;
}
.sidebar::before {
  content: '';
  position: absolute;
  z-index: -1;
  width: 260px;
  height: 210px;
  top: -130px;
  left: -52px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(201, 173, 229, 0.19), rgba(232, 143, 169, 0.07) 40%, transparent 70%);
  filter: blur(12px);
}
.sidebar::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.48;
  background:
    linear-gradient(90deg, transparent 0 86%, rgba(255, 255, 255, 0.045) 86.4%, transparent 87%),
    radial-gradient(ellipse at 95% 12%, rgba(255, 211, 231, 0.16) 0 1px, transparent 1.8px);
  background-size: 100% 100%, 14px 22px;
}
.brand {
  padding: 17px 16px 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  position: relative;
  border-bottom: 1px solid var(--edge-subtle);
  background: linear-gradient(110deg, rgba(255, 255, 255, 0.035), transparent 72%);
}
.brand-mark {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border: 1px solid var(--edge-highlight);
  border-radius: 13px 13px 13px 5px;
  background: linear-gradient(145deg, var(--primary-soft), rgba(255, 255, 255, 0.025));
  box-shadow: inset 0 1px 0 var(--surface-shine), 0 6px 18px rgba(7, 6, 14, 0.12);
}
.brand-logo {
  flex-shrink: 0;
  filter: drop-shadow(0 2px 7px rgba(232, 143, 169, 0.35));
}
.brand-copy {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 1px;
}
.brand-name {
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 1px;
}
.brand-caption {
  color: var(--text-faint);
  font-size: 0.49rem;
  line-height: 1.2;
  letter-spacing: 0.12em;
  white-space: nowrap;
}
.brand-sub {
  font-size: 0.65rem;
  color: var(--text-faint);
  background: rgba(232, 143, 169, 0.1);
  border: 1px solid rgba(232, 143, 169, 0.22);
  color: var(--accent-light);
  border-radius: var(--radius-full);
  padding: 2px 8px;
  margin-left: 2px;
}
.sidebar-sprig {
  height: 11px;
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 17px 3px;
  color: var(--primary-light);
  opacity: 0.55;
}
.sidebar-sprig::before,
.sidebar-sprig::after {
  content: '';
  height: 1px;
  flex: 1;
  background: linear-gradient(90deg, transparent, currentColor);
}
.sidebar-sprig::after { transform: scaleX(-1); }
.sidebar-sprig span,
.sidebar-sprig i {
  width: 4px;
  height: 4px;
  display: block;
  border-radius: 50%;
  background: currentColor;
}
.sidebar-sprig i { width: 6px; height: 6px; background: var(--accent-light); }

/* 搜索框 */
.search-box {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 14px 10px;
}
.search-input {
  flex: 1;
  background: var(--bg-input);
  border: 1px solid transparent;
  box-shadow: inset 0 0 0 1px var(--edge-subtle), inset 0 1px 0 rgba(255, 255, 255, 0.025);
  border-radius: var(--radius-full);
  padding: 7px 12px;
  color: var(--text);
  font-size: 0.8rem;
  outline: none;
  transition: border-color 0.2s;
}
.search-input:focus { border-color: var(--edge-active); box-shadow: var(--glow), inset 0 1px 0 var(--surface-shine); }
.search-input::placeholder { color: var(--text-faint); }
.search-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 1px solid var(--edge-subtle);
  border-radius: var(--radius-full);
  background: var(--bg-card);
  color: var(--text-dim);
  cursor: pointer;
  transition: all 0.15s;
}
.search-btn:hover { border-color: var(--edge-active); color: var(--primary-text); }
.search-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.search-note { font-size: 0.72rem; color: var(--text-faint); padding: 4px 14px; }

.list-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 16px 8px;
}
.list-title {
  font-size: 0.76rem;
  font-weight: 700;
  color: var(--text-dim);
}
.list-hint {
  font-size: 0.64rem;
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
  flex-wrap: wrap;
  gap: 4px 8px;
  margin-bottom: 3px;
  transition: all 0.15s ease;
}
.sitem:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.sitem.active {
  background: var(--primary-soft);
  color: var(--primary-text);
  font-weight: 600;
  box-shadow: inset 0 1px 0 var(--surface-shine), inset 0 0 0 1px var(--edge-highlight);
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
.sitem-preview {
  flex-basis: 100%;
  font-size: 0.72rem;
  color: var(--text-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.moodcard {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin: 6px 10px 2px;
  border-radius: var(--radius-md);
  border-color: var(--edge-highlight);
  background: linear-gradient(135deg, rgba(232, 143, 169, 0.11), rgba(169, 154, 197, 0.11));
  box-shadow: var(--shadow-sm), inset 0 1px 0 rgba(255, 255, 255, 0.05);
}
.moodemoji { font-size: 1.5rem; line-height: 1; }
.moodnum {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.92rem;
  font-weight: 800;
  color: var(--primary-text);
  background: radial-gradient(circle at 32% 28%, var(--primary-soft), var(--bg-hover));
  border: 1px solid var(--border-light);
  box-shadow: var(--shadow-sm);
}
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
  border-top: 1px solid var(--edge-subtle);
}
.settings-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--primary-text);
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
  background: rgba(10, 15, 10, 0.55);
  backdrop-filter: blur(4px);
  z-index: 200;
  display: flex;
  align-items: center;
  justify-content: center;
}
.viewer {
  width: min(640px, 86vw);
  max-height: 78vh;
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
.viewer-export {
  flex-shrink: 0;
  margin-left: auto;
  margin-right: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  color: var(--accent);
  font-size: 0.75rem;
  text-decoration: none;
  align-self: center;
}
.viewer-export:hover { filter: brightness(1.1); }
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
.vmsg.bot .vcontent { background: var(--bg-card); border: 1px solid var(--border); color: var(--text); }
.vimg {
  max-width: 220px;
  border-radius: var(--radius-md);
  margin-top: 4px;
  box-shadow: var(--shadow-sm);
}

/* 日间侧栏保留纸张般的温度，而不是直接把夜间的紫粉光换白。 */
.theme-light .sidebar::before {
  background: radial-gradient(circle, rgba(230, 191, 113, 0.19), rgba(139, 168, 99, 0.10) 42%, transparent 71%);
}
.theme-light .sidebar::after {
  background:
    linear-gradient(90deg, transparent 0 86%, rgba(102, 126, 76, 0.075) 86.4%, transparent 87%),
    radial-gradient(ellipse at 95% 12%, rgba(184, 146, 85, 0.20) 0 1px, transparent 1.8px);
  background-size: 100% 100%, 14px 22px;
}
.theme-light .brand { background: linear-gradient(110deg, rgba(255, 255, 255, 0.55), transparent 72%); }
.theme-light .brand-mark { background: linear-gradient(145deg, rgba(243, 231, 195, 0.72), rgba(255, 255, 255, 0.48)); }
.theme-light .brand-sub { background: rgba(183, 126, 139, 0.10); border-color: rgba(183, 126, 139, 0.22); color: #a56f71; }

@media (max-width: 768px) {
  .sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: 280px;
    z-index: 60;
    transform: translateX(-105%);
    transition: transform 0.28s ease;
    box-shadow: var(--shadow-lg);
  }
  .sidebar.open { transform: translateX(0); }
}
</style>
