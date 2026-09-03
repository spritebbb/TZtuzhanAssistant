<script setup lang="ts">
import { onMounted, ref, watch, onUnmounted } from 'vue'
import SessionList from './components/SessionList.vue'
import ChatView from './components/ChatView.vue'
import Portrait from './components/Portrait.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import AgentPanel from './components/AgentPanel.vue'
import { ensureBaseUrl } from './api'
import { CURRENT_SESSION_ID, archiveCurrent } from './api/sessions'

const settingsOpen = ref(false)
const agentOpen = ref(false)
const sidebarOpen = ref(false)
const currentId = ref<string | null>(CURRENT_SESSION_ID)
const sessionListKey = ref(0)
const chatReloadKey = ref(0)

// 生成中状态（由 ChatView 上报）：流式生成中禁用归档，避免拆对话
const generating = ref(false)
function onStreamingChange(v: boolean) { generating.value = v }

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

// === 主题切换（暗色/亮色，默认暗色） ===
const theme = ref<'dark' | 'light'>('dark')
const themeStorageKey = 'tztuzhan-theme'

function loadTheme() {
  try {
    // 支持 ?theme=light|dark 查询参数显式指定（用于调试/截图验证）
    const q = new URLSearchParams(location.search).get('theme')
    if (q === 'light' || q === 'dark') { theme.value = q; return }
    const saved = localStorage.getItem(themeStorageKey)
    if (saved === 'light' || saved === 'dark') theme.value = saved
  } catch { /* 不可用时保持默认 */ }
}

function applyTheme(t: 'dark' | 'light') {
  document.body.classList.toggle('theme-light', t === 'light')
  try { localStorage.setItem(themeStorageKey, t) } catch { /* ignore */ }
}

function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  applyTheme(theme.value)
}

watch(theme, applyTheme, { immediate: false })

// === 键盘快捷键 ===
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'T') {
    e.preventDefault()
    toggleTheme()
  }
  if (e.key === 'Escape') {
    settingsOpen.value = false
    agentOpen.value = false
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
  applyTheme(theme.value)
  document.addEventListener('keydown', onKeydown)
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
          <div class="name">菟菚</div>
          <div class="sub">细藤缠绕 · 温润坚韧 · 你的拟人助手</div>
        </div>
        <div class="header-right">
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
        </div>
      </header>
      <ChatView :session-id="currentId" :reload-key="chatReloadKey" @open-settings="openSettings" @archived="onArchived" @request-archive="archiveNow" @streaming-change="onStreamingChange" />
    </div>

    <!-- 面板 -->
    <SettingsPanel :show="settingsOpen" @close="closeSettings" />
    <AgentPanel :show="agentOpen" @close="agentOpen = false" />
  </div>
</template>

<style scoped>
.app-root {
  display: flex;
  height: 100vh;
  overflow: hidden;
  position: relative;
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
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  z-index: 10;
}
.header-left { flex-shrink: 0; }
.header-center {
  flex: 1;
  min-width: 0;
}
.header-center .name {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.5px;
}
.header-center .sub {
  font-size: 0.73rem;
  color: var(--text-faint);
  margin-top: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
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
}
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
  .header { padding: 10px 14px; }
  .header-center .sub { display: none; }
}
</style>