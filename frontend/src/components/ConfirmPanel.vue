<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { apiFetch } from '../api'

const props = defineProps<{
  pending: PendingRequest[]
}>()

const emit = defineEmits<{
  (e: 'resolve', requestId: string, allow: boolean, remember: boolean): void
}>()

export interface PendingRequest {
  request_id: string
  tool: string
  args: Record<string, string>
  danger: string
  message: string
  timeout: number
}

const responding = ref<Set<string>>(new Set())
const remain = ref<Record<string, number>>({})
const timers = new Map<string, ReturnType<typeof setInterval>>()

async function respond(requestId: string, allow: boolean) {
  responding.value.add(requestId)
  try {
    const r = await apiFetch('/api/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ request_id: requestId, allow: String(allow) }).toString(),
    })
    const data = await r.json()
    if (data.ok) {
      emit('resolve', requestId, allow, false)
    }
  } catch {
    emit('resolve', requestId, false, false)
  } finally {
    responding.value.delete(requestId)
  }
}

watch(() => props.pending.map(p => p.request_id), (ids) => {
  for (const [rid, timer] of timers) {
    if (!ids.includes(rid)) {
      clearInterval(timer)
      timers.delete(rid)
    }
  }
  for (const p of props.pending) {
    if (timers.has(p.request_id)) continue
    remain.value[p.request_id] = p.timeout || 60
    timers.set(p.request_id, setInterval(() => {
      const cur = (remain.value[p.request_id] ?? 1) - 1
      if (cur <= 0) {
        clearInterval(timers.get(p.request_id))
        timers.delete(p.request_id)
        delete remain.value[p.request_id]
        emit('resolve', p.request_id, false, false)
      } else {
        remain.value[p.request_id] = cur
      }
    }, 1000))
  }
}, { immediate: true })

onBeforeUnmount(() => {
  for (const timer of timers.values()) clearInterval(timer)
  timers.clear()
})

function dangerColor(d: string): string {
  if (d === 'high' || d === 'critical') return 'var(--danger)'
  if (d === 'normal') return 'var(--accent)'
  return 'var(--primary)'
}

function dangerLabel(d: string): string {
  if (d === 'critical') return '⚠️ 危险'
  if (d === 'high') return '⚠️ 高风险'
  if (d === 'normal') return '⚡ 常规'
  return 'ℹ️ 信息'
}
</script>

<template>
  <div v-if="props.pending.length > 0" class="confirm-overlay">
    <div class="confirm-panel glass" v-for="pr in props.pending" :key="pr.request_id">
      <div class="confirm-header">
        <span class="danger-badge" :style="{ background: dangerColor(pr.danger) }">{{ dangerLabel(pr.danger) }}</span>
        <span class="confirm-tool"><code>{{ pr.tool }}</code></span>
        <span class="confirm-countdown" v-if="remain[pr.request_id] !== undefined">⏳ {{ remain[pr.request_id] }}s 后自动拒绝</span>
      </div>
      <div class="confirm-msg">{{ pr.message }}</div>
      <div class="confirm-args" v-if="Object.keys(pr.args).length > 0">
        <div class="arg-row" v-for="(v, k) in pr.args" :key="k">
          <span class="arg-key">{{ k }}:</span>
          <span class="arg-val">{{ v }}</span>
        </div>
      </div>
      <div class="confirm-actions">
        <button
          class="cf-btn reject"
          :disabled="responding.has(pr.request_id)"
          @click="respond(pr.request_id, false)"
        >拒绝</button>
        <button
          class="cf-btn allow"
          :disabled="responding.has(pr.request_id)"
          @click="respond(pr.request_id, true)"
        >允许</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.confirm-overlay {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px 22px 4px;
  flex-shrink: 0;
}
.confirm-panel {
  border-left: 4px solid var(--accent);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  box-shadow: var(--shadow-md);
  animation: fadeUp 0.25s ease both;
  max-width: 560px;
}
.confirm-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.danger-badge {
  font-size: 0.7rem;
  color: #fff;
  padding: 2px 10px;
  border-radius: 20px;
  font-weight: 600;
  letter-spacing: 0.3px;
}
.confirm-tool {
  font-size: 0.82rem;
  color: var(--text-dim);
}
.confirm-tool code {
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 0.8rem;
}
.confirm-countdown {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--accent);
  white-space: nowrap;
}
.confirm-msg {
  font-size: 0.9rem;
  color: var(--text);
  margin-bottom: 6px;
  line-height: 1.5;
}
.confirm-args {
  margin-bottom: 10px;
  font-size: 0.8rem;
}
.arg-row {
  display: flex;
  gap: 6px;
  padding: 2px 0;
}
.arg-key {
  color: var(--text-faint);
  min-width: 60px;
  flex-shrink: 0;
}
.arg-val {
  color: var(--text-dim);
  word-break: break-all;
  max-height: 60px;
  overflow: hidden;
}
.confirm-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.cf-btn {
  border: none;
  border-radius: 10px;
  padding: 8px 22px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.cf-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.cf-btn.reject {
  background: var(--danger-soft);
  color: var(--danger);
}
.cf-btn.reject:hover { background: rgba(224, 138, 109, 0.22); }
.cf-btn.allow {
  background: var(--primary-soft);
  color: var(--primary-text);
  border: 1px solid var(--border-light);
}
.cf-btn.allow:hover { background: var(--primary); color: var(--text-invert); }
</style>