<script setup lang="ts">
import { onMounted, ref } from 'vue'
import SessionList from './components/SessionList.vue'
import ChatView from './components/ChatView.vue'
import Portrait from './components/Portrait.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import AgentPanel from './components/AgentPanel.vue'
import { ensureBaseUrl } from './api'
import { CURRENT_SESSION_ID } from './api/sessions'

const settingsOpen = ref(false)
const agentOpen = ref(false)
const sidebarOpen = ref(false)
// 单一会话：id 固定为 'current'，不再新建/切换
const currentId = ref<string | null>(CURRENT_SESSION_ID)
// 归档列表刷新信号：归档后侧栏需要重新拉取
const sessionListKey = ref(0)

function openSettings() {
  settingsOpen.value = true
}
function closeSettings() {
  settingsOpen.value = false
}

// 归档完成：清空当前会话显示，刷新归档列表
function onArchived() {
  currentId.value = CURRENT_SESSION_ID
  sessionListKey.value += 1
}

onMounted(async () => {
  await ensureBaseUrl()
})
</script>

<template>
  <div class="app-root">
    <!-- 背景藤蔓装饰 -->
    <div class="vine-bg" aria-hidden="true">
      <svg viewBox="0 0 200 200" preserveAspectRatio="none" class="vine vine-tl">
        <path d="M0 200 C 30 170, 20 130, 50 105 S 85 60, 70 25 S 90 0, 120 0" fill="none" stroke="#c6d680" stroke-width="3" stroke-linecap="round" opacity="0.35"/>
        <path d="M45 120 C 60 108, 68 92, 58 78" fill="none" stroke="#d9a860" stroke-width="2" stroke-linecap="round" opacity="0.3"/>
      </svg>
      <svg viewBox="0 0 200 200" preserveAspectRatio="none" class="vine vine-br">
        <path d="M200 0 C 170 25, 175 70, 150 95 S 115 140, 132 178 S 115 200, 90 200" fill="none" stroke="#c6d680" stroke-width="3" stroke-linecap="round" opacity="0.3"/>
        <path d="M148 100 C 135 112, 130 128, 140 142" fill="none" stroke="#d9a860" stroke-width="2" stroke-linecap="round" opacity="0.28"/>
      </svg>
    </div>

    <!-- 左侧归档列表栏 -->
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
        <button class="menubtn" title="归档列表" @click="sidebarOpen = !sidebarOpen">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
            <line x1="3" y1="6" x2="21" y2="6"/>
            <line x1="3" y1="12" x2="21" y2="12"/>
            <line x1="3" y1="18" x2="21" y2="18"/>
          </svg>
        </button>
        <div class="header-left">
          <Portrait :size="42" />
        </div>
        <div class="header-center">
          <div class="name">菟菚</div>
          <div class="sub">细藤缠绕 · 温润坚韧 · 你的拟人助手</div>
        </div>
        <div class="header-right">
          <button class="icon-btn" title="任务代理" @click="agentOpen = !agentOpen">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
          </button>
          <button class="icon-btn" title="设置" @click="openSettings">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </button>
        </div>
      </header>
      <ChatView :session-id="currentId" @open-settings="openSettings" @archived="onArchived" />
    </div>

    <!-- 设置面板 -->
    <SettingsPanel :show="settingsOpen" @close="closeSettings" />

    <!-- 任务代理面板 -->
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
.vine-bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}
.vine {
  position: absolute;
  width: 200px;
  height: 200px;
  overflow: visible;
}
.vine-tl { top: 0; left: 0; animation: sway 7s ease-in-out infinite; transform-origin: top left; }
.vine-br { bottom: 0; right: 0; animation: sway 9s ease-in-out infinite reverse; transform-origin: bottom right; }

.header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 22px;
  background: var(--bg-header);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
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
  gap: 6px;
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
  color: var(--primary-deep);
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
  color: var(--primary-deep);
  background: var(--border);
}

@media (max-width: 720px) {
  .menubtn {
    display: flex;
  }
  .header {
    padding: 10px 14px;
  }
}
</style>
