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
// 每个请求的剩余秒数（倒计时显示）
const remain = ref<Record<string, number>>({})
// 定时器句柄
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
    // 网络错误：确认请求可能已超时，前端自动移除
    emit('resolve', requestId, false, false)
  } finally {
    responding.value.delete(requestId)
  }
}

// 对每个新到的确认请求启动倒计时：到期自动按拒绝移除（后端同样会超时拒绝）
watch(() => props.pending.map(p => p.request_id), (ids) => {
  // 清理已不在列表中的请求计时器
  for (const [rid, timer] of timers) {
    if (!ids.includes(rid)) {
      clearInterval(timer)
      timers.delete(rid)
    }
  }
  // 为新请求启动倒计时
  for (const p of props.pending) {
    if (timers.has(p.request_id)) continue
    remain.value[p.request_id] = p.timeout || 60
    timers.set(p.request_id, setInterval(() => {
      const cur = (remain.value[p.request_id] ?? 1) - 1
      if (cur <= 0) {
        clearInterval(timers.get(p.request_id))
        timers.delete(p.request_id)
        delete remain.value[p.request_id]
        emit('resolve', p.request_id, false, false) // 超时 → 按拒绝移除
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
  if (d === 'high' || d === 'critical') return '#e0584a'
  if (d === 'normal') return '#d9a860'
  return '#a4b85c'
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
    <div class="confirm-panel" v-for="pr in props.pending" :key="pr.request_id">
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
  background: #fffdf5;
  border: 1px solid #e8e0c8;
  border-left: 4px solid #d9a860;
  border-radius: 14px;
  padding: 12px 16px;
  box-shadow: 0 6px 24px rgba(40, 50, 25, 0.14);
  animation: slideUp 0.25s ease both;
  max-width: 560px;
}
@keyframes slideUp {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
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
  color: #6a6048;
}
.confirm-tool code {
  background: #f0ede4;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 0.8rem;
}
.confirm-countdown {
  margin-left: auto;
  font-size: 0.7rem;
  color: #b09a68;
  white-space: nowrap;
}
.confirm-msg {
  font-size: 0.9rem;
  color: #3a3428;
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
  color: #8a7e66;
  min-width: 60px;
  flex-shrink: 0;
}
.arg-val {
  color: #3a3428;
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
  background: #f5ede8;
  color: #8a6050;
}
.cf-btn.reject:hover { background: #ecdcd4; }
.cf-btn.allow {
  background: #c6d680;
  color: #3a4428;
}
.cf-btn.allow:hover { background: #b8c86e; }
</style>