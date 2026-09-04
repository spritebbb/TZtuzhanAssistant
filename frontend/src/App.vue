<script setup lang="ts">
import { onMounted, ref, watch, onUnmounted } from 'vue'
import SessionList from './components/SessionList.vue'
import ChatView from './components/ChatView.vue'
import Portrait from './components/Portrait.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import AgentPanel from './components/AgentPanel.vue'
import DiaryPanel from './components/DiaryPanel.vue'
import MemoryPanel from './components/MemoryPanel.vue'
import UsagePanel from './components/UsagePanel.vue'
import { ensureBaseUrl, apiFetch } from './api'
import { CURRENT_SESSION_ID, archiveCurrent, resetUser } from './api/sessions'

const settingsOpen = ref(false)
const agentOpen = ref(false)
const diaryOpen = ref(false)
const memoryOpen = ref(false)
const usageOpen = ref(false)
const sidebarOpen = ref(false)
const currentId = ref<string | null>(CURRENT_SESSION_ID)
const sessionListKey = ref(0)
const chatReloadKey = ref(0)

// 生成中状态（由 ChatView 上报）：流式生成中禁用归档，避免拆对话
const generating = ref(false)
function onStreamingChange(v: boolean) {
  generating.value = v
  // 一条回复流结束（v=false）＝好感度可能刚变动过 → 顺手刷新顶部好感度条
  if (!v) refreshAffection()
}

// === 顶部好感度条：从 /api/meta 读取好感度/阶段/羁绊 ===
const affection = ref({ value: 0, stage: '初识', bond: '', next: '熟悉', next_at: 25, fill: 0 })
async function refreshAffection() {
  try {
    const r = await apiFetch('/api/meta')
    const d = await r.json()
    if (d.affection) affection.value = d.affection
  } catch { /* 好感度拉取失败保留旧值 */ }
}

// === 主动归档当前对话 ===
const archiving = ref(false)
async function archiveNow() {
  if (archiving.value || generating.value) return
  archiving.value = true
  try {
    const info = await archiveCurrent()
    if (info) {
      // 归档成功：刷新侧栏归档列表 + 强制重载对话区（清空消息）
      sessionListKey.value += 1
      chatReloadKey.value += 1
    } else {
      // 当前会话没有可归档内容
      window.alert?.('当前会话还没有可归档的内容')
    }
  } catch {
    window.alert?.('归档失败，请稍后重试')
  } finally {
    archiving.value = false
  }
}

// === 彻底重置（失忆重开）：清空菟菚记忆/好感/昵称/向量 + 当前会话 ===
const resetOpen = ref(false)   // 确认弹窗
const resetting = ref(false)   // 重置进行中
function openResetConfirm() {
  if (resetting.value || generating.value) return
  resetOpen.value = true
}
function closeResetConfirm() {
  if (!resetting.value) resetOpen.value = false
}
async function doReset() {
  if (resetting.value) return
  resetting.value = true
  try {
    await resetUser()
    // 重置成功：侧栏（心情/记忆/归档视图复位）+ 对话区重载到全新空白会话
    sessionListKey.value += 1
    chatReloadKey.value += 1
    currentId.value = CURRENT_SESSION_ID
    resetOpen.value = false
    refreshAffection()  // 失忆后好感度归零，刷新顶部条
  } catch {
    window.alert?.('重置失败，请稍后重试')
  } finally {
    resetting.value = false
  }
}

const theme = ref<'dark' | 'light'>('dark')
const themeStorageKey = 'tztuzhan-theme'

// === 主题切换（暗色/亮色；未手动选择时按昼夜自动：7-19 点亮色温室，夜晚暗色月光） ===
function loadTheme() {
  try {
    // 支持 ?theme=light|dark 查询参数显式指定（用于调试/截图验证）
    const q = new URLSearchParams(location.search).get('theme')
    if (q === 'light' || q === 'dark') { theme.value = q; return }
    const saved = localStorage.getItem(themeStorageKey)
    if (saved === 'light' || saved === 'dark') { theme.value = saved; return }
    // 自动模式：跟随本地时间，不落盘——用户手动切换后才固定偏好
    const hour = new Date().getHours()
    theme.value = hour >= 7 && hour < 19 ? 'light' : 'dark'
  } catch { /* 不可用时保持默认 */ }
}

function applyTheme(t: 'dark' | 'light', persist = true) {
  document.body.classList.toggle('theme-light', t === 'light')
  if (persist) {
    try { localStorage.setItem(themeStorageKey, t) } catch { /* ignore */ }
  }
}

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyTheme(theme.value, true)  // 手动切换才固定偏好
}

watch(theme, (t) => applyTheme(t, false), { immediate: false })

// === 键盘快捷键 ===
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'T') {
    e.preventDefault()
    toggleTheme()
  }
  if (e.key === 'Escape') {
    settingsOpen.value = false
    agentOpen.value = false
    diaryOpen.value = false
    memoryOpen.value = false
    usageOpen.value = false
    sidebarOpen.value = false
  }
}

function openSettings() { settingsOpen.value = true }
function closeSettings() { settingsOpen.value = false }
function onArchived() {
  currentId.value = CURRENT_SESSION_ID
  sessionListKey.value += 1
}

onMounted(async () => {
  await ensureBaseUrl()
  loadTheme()
  applyTheme(theme.value, false)  // 初始应用不落盘：自动模式每天重新判断
  document.addEventListener('keydown', onKeydown)
  refreshAffection()  // 首屏载入好感度条
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="app-root">
    <!-- 侧栏 -->
    <SessionList
      :key="sessionListKey"
      :open="sidebarOpen"
      :current-id="currentId"
      @close-sidebar="sidebarOpen = false"
      @open-settings="openSettings"
    />

    <!-- 主区 -->
    <div class="main">
      <header class="header">
        <button class="menubtn" title="切换侧栏" @click="sidebarOpen = !sidebarOpen">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
            <line x1="3" y1="6" x2="21" y2="6"/>
            <line x1="3" y1="12" x2="13" y2="12"/>
          </svg>
        </button>
        <div class="header-left">
          <Portrait :size="40" />
        </div>
        <div class="header-center">
          <div class="header-kicker"><span></span>BOTANICAL COMPANION</div>
          <div class="name"><span>菟菚</span><i>·</i><em>cuscuta chinensis</em></div>
          <div class="sub">细藤缠绕 · 温润坚韧 · 你的拟人助手</div>
        </div>
        <div class="header-sprig" aria-hidden="true"><span></span><i></i><span></span></div>
        <div class="presence" title="菟菚正在陪伴你">
          <span class="presence-dot"></span>
          陪伴中
        </div>
        <div class="header-right">
          <button class="icon-btn" title="偷看菟菚的日记" @click="diaryOpen = true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
            </svg>
          </button>
          <button class="icon-btn" title="她记住的事（可改写/删除）" @click="memoryOpen = true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3a7 7 0 0 0-7 7c0 2.4 1.2 4.5 3 5.7V19a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-3.3c1.8-1.2 3-3.3 3-5.7a7 7 0 0 0-7-7z"/>
            </svg>
          </button>
          <button class="icon-btn" title="养她的账本（token 用量）" @click="usageOpen = true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 3v18h18"/><path d="M7 15l4-4 4 3 5-6"/>
            </svg>
          </button>
          <button class="icon-btn" title="切换主题（Ctrl+Shift+T）" @click="toggleTheme">
            <svg v-if="theme === 'dark'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="5"/>
              <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
            </svg>
            <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
            </svg>
          </button>
          <button class="icon-btn" title="任务代理" @click="agentOpen = !agentOpen">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
            </svg>
          </button>
          <button class="icon-btn" :title="archiving ? '归档中…' : generating ? '正在生成回复，暂不能归档' : '归档当前对话'" :disabled="archiving || generating" @click="archiveNow">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 8v13H3V8"/>
              <path d="M1 3h22v5H1z"/>
              <path d="M10 12h4"/>
            </svg>
          </button>
          <button class="icon-btn" title="设置" @click="openSettings">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </button>
          <button class="icon-btn reset-btn" title="重新开始（让菟菚忘记你）" @click="openResetConfirm">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="1 4 1 10 7 10"/>
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>
            </svg>
          </button>
        </div>
      </header>
      <!-- 好感度条：细藤对你的依赖与亲近程度 -->
      <div class="aff-bar">
        <svg class="aff-heart" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M12 21s-6.7-4.4-9.3-8.5C.6 9.2 2.3 5.5 5.7 5.5c2 0 3.4 1.1 4.3 2.5h4c.9-1.4 2.3-2.5 4.3-2.5 3.4 0 5.1 3.7 3 7-2.6 4.1-9.3 8.5-9.3 8.5z"/>
        </svg>
        <div class="aff-track">
          <div class="aff-fill" :style="{ width: Math.min(100, affection.fill) + '%' }"></div>
        </div>
        <span class="aff-label">{{ affection.value }} · {{ affection.bond || affection.stage }}</span>
        <span v-if="affection.next" class="aff-next" :title="'距「' + affection.next + '」还需 ' + (affection.next_at - affection.value) + ' 点'">→ {{ affection.next }} {{ affection.next_at - affection.value }}</span>
        <span v-else class="aff-next max">♥ 已至圆满</span>
      </div>
      <ChatView :session-id="currentId" :reload-key="chatReloadKey" @open-settings="openSettings" @archived="onArchived" @request-archive="archiveNow" @streaming-change="onStreamingChange" />
    </div>

    <!-- 面板 -->
    <SettingsPanel :show="settingsOpen" @close="closeSettings" />
    <AgentPanel :show="agentOpen" @close="agentOpen = false" />
    <DiaryPanel :show="diaryOpen" @close="diaryOpen = false" />
    <MemoryPanel :show="memoryOpen" @close="memoryOpen = false" />
    <UsagePanel :show="usageOpen" @close="usageOpen = false" />

    <!-- 彻底重置确认弹窗 -->
    <div v-if="resetOpen" class="modal-mask" @click.self="closeResetConfirm">
      <div class="modal reset-modal">
        <div class="modal-title">重新开始？</div>
        <div class="modal-body">
          <p>这会让菟菚<b>忘记你积累的一切</b>：</p>
          <ul>
            <li>她对你的好感度、给你的昵称、恋人关系</li>
            <li>她的记忆、你告诉她的事、共同回忆、向量库</li>
            <li>当前这段对话的气泡</li>
          </ul>
          <p class="muted">回到最开始的「初识」状态。此操作<b>不可撤销</b>（已归档的对话仍保留，可在侧栏查看）。</p>
        </div>
        <div class="modal-actions">
          <button class="btn ghost" :disabled="resetting" @click="closeResetConfirm">取消</button>
          <button class="btn danger" :disabled="resetting" @click="doReset">{{ resetting ? '重置中…' : '确认重置' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.app-root {
  display: flex;
  height: 100vh;
  overflow: hidden;
  position: relative;
  isolation: isolate;
}
.app-root::before,
.app-root::after {
  content: '';
  position: absolute;
  pointer-events: none;
  z-index: -1;
  border-radius: 50%;
  filter: blur(28px);
}
.app-root::before {
  width: 340px;
  height: 340px;
  right: 7%;
  top: -180px;
  background: rgba(172, 146, 211, 0.15);
}
.app-root::after {
  width: 270px;
  height: 270px;
  left: 16%;
  bottom: -190px;
  background: rgba(232, 143, 169, 0.09);
}
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  position: relative;
  z-index: 1;
}

.header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 22px;
  background: var(--bg-header);
  backdrop-filter: blur(20px) saturate(1.3);
  -webkit-backdrop-filter: blur(20px) saturate(1.3);
  border-bottom: 1px solid var(--edge-subtle);
  box-shadow: inset 0 -1px 0 rgba(7, 5, 14, 0.12), 0 8px 24px rgba(9, 7, 17, 0.06);
  flex-shrink: 0;
  z-index: 10;
  position: relative;
  overflow: hidden;
}
.header::before {
  content: '';
  position: absolute;
  width: 310px;
  height: 116px;
  right: 18%;
  top: -76px;
  border-radius: 50%;
  background: radial-gradient(ellipse, rgba(231, 178, 207, 0.14), rgba(174, 154, 210, 0.08) 45%, transparent 72%);
  filter: blur(10px);
  pointer-events: none;
}
.header::after {
  content: '';
  position: absolute;
  right: 144px;
  bottom: 0;
  width: 145px;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--primary-light), var(--accent-light), transparent);
  opacity: 0.48;
  pointer-events: none;
}
.header-left {
  flex-shrink: 0;
  position: relative;
  z-index: 1;
}
.header-left::before {
  content: '';
  position: absolute;
  inset: -4px;
  border: 1px solid var(--edge-highlight);
  border-radius: 14px 14px 14px 6px;
  background: var(--primary-soft);
  z-index: -1;
  box-shadow: 0 4px 12px rgba(8, 7, 15, 0.10);
}
.header-center {
  flex: 1;
  min-width: 0;
  position: relative;
  z-index: 1;
}
.header-kicker {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 1px;
  color: var(--primary-light);
  font-size: 0.48rem;
  letter-spacing: 0.15em;
  line-height: 1;
}
.header-kicker span {
  width: 12px;
  height: 1px;
  background: currentColor;
  opacity: 0.72;
}
.header-center .name {
  display: flex;
  align-items: baseline;
  gap: 5px;
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.5px;
}
.header-center .name i {
  color: var(--accent);
  font-style: normal;
  font-weight: 400;
  opacity: 0.78;
}
.header-center .name em {
  color: var(--text-faint);
  font-size: 0.58rem;
  font-family: Georgia, serif;
  font-weight: 400;
  letter-spacing: 0.04em;
}
.header-center .sub {
  font-size: 0.73rem;
  color: var(--text-faint);
  margin-top: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.header-sprig {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 74px;
  color: var(--primary-light);
  opacity: 0.52;
  position: relative;
  z-index: 1;
}
.header-sprig::before,
.header-sprig::after {
  content: '';
  height: 1px;
  flex: 1;
  background: currentColor;
}
.header-sprig span,
.header-sprig i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
}
.header-sprig i { width: 6px; height: 6px; background: var(--accent-light); }
.presence {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--edge-subtle);
  border-radius: var(--radius-full);
  background: rgba(255, 255, 255, 0.035);
  color: var(--primary-text);
  font-size: 0.7rem;
  white-space: nowrap;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
  position: relative;
  z-index: 1;
}
.presence-dot {
  width: 6px;
  height: 6px;
  background: var(--ok);
  border-radius: 50%;
  box-shadow: 0 0 8px var(--ok);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
  position: relative;
  z-index: 1;
}
.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  transition: all 0.18s ease;
}
.icon-btn:hover {
  background: var(--bg-hover);
  color: var(--primary-text);
  box-shadow: inset 0 0 0 1px var(--edge-subtle), 0 3px 10px rgba(8, 7, 15, 0.08);
}

.theme-light .header {
  background: linear-gradient(105deg, rgba(255, 253, 246, 0.91), rgba(249, 244, 232, 0.78));
  box-shadow: inset 0 -1px 0 rgba(105, 123, 76, 0.12), 0 8px 24px rgba(104, 90, 51, 0.07);
}
.theme-light .header::before { background: radial-gradient(ellipse, rgba(241, 202, 130, 0.20), rgba(162, 187, 119, 0.10) 47%, transparent 72%); }
.theme-light .header-left::before { background: rgba(255, 253, 246, 0.72); }
.theme-light .presence { background: rgba(255, 253, 246, 0.62); box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.80); }
.menubtn {
  display: none;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.18s ease;
}
.menubtn:hover {
  color: var(--primary-text);
  background: var(--border);
}

@media (max-width: 768px) {
  .menubtn { display: flex; }
  .header { gap: 8px; padding: 10px 14px; }
  .header-kicker, .header-sprig, .header-center .name em, .header-center .name i { display: none; }
  .header-center .sub { display: none; }
  .header-right { gap: 0; }
  .header-right .icon-btn { width: 33px; height: 34px; }
}

/* 彻底重置按钮：悬停时呈警示色，提示这是危险操作 */
.reset-btn:hover { color: var(--danger); background: var(--danger-soft); }

/* === 顶部好感度条 === */
.aff-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  padding: 8px 22px;
  background: linear-gradient(90deg, rgba(232, 143, 169, 0.06), var(--bg-header) 30%, var(--bg-header));
  border-bottom: 1px solid var(--edge-subtle);
  box-shadow: inset 0 -1px 0 rgba(7, 5, 14, 0.1);
  z-index: 9;
}
.aff-heart {
  color: var(--accent, #e08aa0);
  flex-shrink: 0;
  filter: drop-shadow(0 0 3px color-mix(in srgb, var(--accent, #e08aa0) 60%, transparent));
}
.aff-track {
  flex: 1;
  min-width: 60px;
  height: 6px;
  background: var(--bg-hover);
  border-radius: var(--radius-full);
  overflow: hidden;
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.16), 0 0 0 1px var(--edge-subtle);
}
.aff-fill {
  height: 100%;
  border-radius: var(--radius-full);
  background: linear-gradient(90deg, var(--accent-light, #ffd6e2), var(--accent, #e89bb1), var(--primary, #a99ac5));
  box-shadow: 0 0 8px color-mix(in srgb, var(--accent, #e07a9a) 55%, transparent);
  transition: width 0.7s cubic-bezier(0.22, 1, 0.36, 1);
}
.aff-label {
  font-size: 0.74rem;
  font-weight: 700;
  color: var(--text-dim);
  white-space: nowrap;
  flex-shrink: 0;
}
.aff-next {
  font-size: 0.68rem;
  color: var(--text-faint);
  white-space: nowrap;
  flex-shrink: 0;
}
.aff-next.max { color: var(--accent, #e07a9a); font-weight: 600; }

@media (max-width: 768px) {
  .aff-bar { padding: 6px 14px; }
  .aff-next { display: none; }
  .presence { display: none; }
}

/* 重置确认弹窗 */
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(2px);
}
.modal {
  width: min(420px, 88vw);
  background: var(--bg-panel, #fff);
  border: 1px solid var(--edge-highlight);
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35), inset 0 1px 0 var(--surface-shine);
  border-radius: var(--radius-lg, 14px);
  padding: 20px 22px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
}
.modal-title { font-size: 1.05rem; font-weight: 700; color: var(--text); margin-bottom: 12px; }
.modal-body { color: var(--text-dim); font-size: 0.88rem; line-height: 1.7; }
.modal-body ul { margin: 6px 0 4px; padding-left: 18px; }
.modal-body .muted { color: var(--text-faint); font-size: 0.8rem; margin-top: 8px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
.modal-actions .btn {
  padding: 7px 16px;
  border-radius: var(--radius-sm, 8px);
  border: 1px solid var(--border);
  font-size: 0.86rem;
  cursor: pointer;
  transition: all 0.18s ease;
  background: transparent;
  color: var(--text-dim);
}
.modal-actions .btn:hover { color: var(--text); border-color: var(--edge-active); }
.modal-actions .btn.danger { background: var(--danger); color: #fff; border-color: var(--danger); }
.modal-actions .btn.danger:hover { filter: brightness(1.08); }
.modal-actions .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
</style>
