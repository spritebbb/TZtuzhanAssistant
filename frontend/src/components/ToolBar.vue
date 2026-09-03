<script setup lang="ts">
import { onMounted, ref, onUnmounted } from 'vue'
import { apiFetch } from '../api'

const tools = ref({
  search: false,
  weather: false,
  image: false,
  vision: false,
  memory: true,
  mcp: true,
})
const backendOk = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const labels: Record<string, string> = {
  search: '联网',
  weather: '天气',
  image: '生图',
  vision: '识图',
  memory: '记忆',
  mcp: 'MCP',
}

// 单色 SVG 图标（stroke 继承 currentColor，天然融入主题）
const iconPaths: Record<string, string> = {
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm10 2l-4.35-4.35',
  weather: 'M20 10h-2M20 14h-2M12 6V4M12 22v-2M3 13h2M3 9h2M4 17a4 4 0 0 1 0-8c.9 0 1.7.3 2.4.8A6 6 0 0 1 18 11a3 3 0 0 1 0 6h-1',
  image: 'M3 3h18v18H3zM3 15l5-5 4 4 3-3 6 6M8 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2z',
  vision: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zm11 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  memory: 'M12 2l2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4L4.2 7.7l5.4-.8z',
  mcp: 'M4 5h16v14H4zM4 10h16M9 5v14',
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
  timer = setInterval(checkBackend, 30000)
  // 设置面板保存后刷新工具开关（联网/天气/生图/识图等可能因配置变化）
  window.addEventListener('tztuzhan:config-saved', loadTools)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('tztuzhan:config-saved', loadTools)
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
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path :d="iconPaths[name] || ''"/>
      </svg>
      {{ labels[name] || name }}
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
  padding: 8px 22px;
  background: var(--bg-header);
  border-bottom: 1px solid var(--border);
  font-size: 0.72rem;
  color: var(--text-faint);
  overflow-x: auto;
  white-space: nowrap;
  scrollbar-width: none;
  flex-shrink: 0;
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
  gap: 6px;
  padding: 4px 11px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border-muted);
  background: var(--bg-card);
  color: var(--text-faint);
  transition: all 0.18s ease;
}
.chip svg { flex-shrink: 0; }
.chip .st {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}
.chip.on {
  color: var(--primary-text);
  border-color: var(--border-light);
  background: var(--primary-soft);
}
.chip.on .st {
  background: var(--primary);
}
.chip.off {
  color: var(--text-faint);
  border-color: var(--border-muted);
}
.chip.off .st { background: var(--text-faint); }
.backend-status {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 11px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border-muted);
  background: var(--bg-card);
  color: var(--ok);
  font-size: 0.72rem;
  flex-shrink: 0;
}
.backend-status .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--ok);
}
.backend-status.off { color: var(--danger); border-color: var(--danger-soft); background: var(--danger-soft); }
.backend-status.off .dot { background: var(--danger); }
</style>