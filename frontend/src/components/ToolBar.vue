<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiFetch, getBaseUrl } from '../api'

const tools = ref({
  search: false,
  weather: false,
  image: false,
  vision: false,
  memory: true,
  mcp: true,
})
const backendOk = ref(true)

const labels: Record<string, string> = {
  search: '联网搜索',
  weather: '天气',
  image: '文生图',
  vision: '识图',
  memory: '记忆',
  mcp: 'MCP',
}

const icons: Record<string, string> = {
  search: '🔎',
  weather: '⛅',
  image: '🎨',
  vision: '👁️',
  memory: '🧠',
  mcp: '🧰',
}

async function loadTools() {
  try {
    const r = await apiFetch('/api/meta')
    const d = await r.json()
    if (d.tools) Object.assign(tools.value, d.tools)
  } catch { /* ignore */ }
}

async function checkBackend() {
  try {
    const r = await apiFetch('/api/health')
    backendOk.value = r.ok
  } catch {
    backendOk.value = false
  }
}

onMounted(() => {
  loadTools()
  checkBackend()
  setInterval(checkBackend, 30000)
})
</script>

<template>
  <div class="toolbar">
    <span class="label">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
      </svg>
      工具
    </span>
    <span v-for="(on, name) in tools" :key="name" class="chip" :class="on ? 'on' : 'off'" :title="on ? '已启用' : '未启用'">
      <span class="ic">{{ icons[name] || '' }}</span>{{ labels[name] || name }}
      <span class="st"></span>
    </span>
    <span class="backend-status" :class="{ off: !backendOk }" :title="backendOk ? '后端连接正常' : '后端连接失败，请检查 8801 端口'">
      <span class="dot"></span>{{ backendOk ? '后端在线' : '后端离线' }}
    </span>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 22px;
  background: rgba(237, 244, 240, 0.7);
  border-bottom: 1px solid var(--border-light);
  font-size: 0.72rem;
  color: var(--text-faint);
  overflow-x: auto;
  white-space: nowrap;
  scrollbar-width: none;
}
.toolbar::-webkit-scrollbar { display: none; }
.label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-right: 4px;
  color: var(--text-dim);
  font-weight: 600;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text-faint);
  transition: all 0.18s ease;
}
.chip .ic { font-size: 0.82rem; }
.chip .st {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.chip.on {
  color: var(--primary-deep);
  border-color: #d5e0a8;
  background: var(--primary-soft);
}
.chip.on .st {
  background: var(--primary);
  box-shadow: 0 0 5px var(--primary);
}
.chip.off {
  color: #aab39c;
  border-color: var(--border-light);
}
.chip.off .st { background: #c4cbb5; }
.backend-status {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
  background: #fff;
  color: var(--primary-deep);
  font-size: 0.72rem;
}
.backend-status .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--primary);
  box-shadow: 0 0 5px var(--primary);
}
.backend-status.off { color: var(--danger); border-color: var(--danger-soft); background: var(--danger-soft); }
.backend-status.off .dot { background: var(--danger); box-shadow: none; }
</style>
