<script setup lang="ts">
import { ref, watch } from 'vue'
import { apiFetch, getBaseUrl } from '../api'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

interface TaskBrief {
  id: string
  objective: string
  status: string
  created_at: number
  updated_at: number
}
interface Step {
  title: string
  detail: string
  status: string
  result: string
  ts: number
}
interface TaskDetail {
  id: string
  objective: string
  plan: Step[]
  status: string
  step_confirmations: Record<string, string>
  pending_steps: number[]
  result: string
  log: any[]
  created_at: number
  updated_at: number
}

const tasks = ref<TaskBrief[]>([])
const current = ref<TaskDetail | null>(null)
const objective = ref('')
const busy = ref(false)
const msg = ref('')
const running = ref(false)
const pendingConfirms = ref<PendingConfirm[]>([])

interface PendingConfirm {
  request_id: string
  tool: string
  args: Record<string, string>
  danger: string
  message: string
  timeout: number
}

let streamCtrl: AbortController | null = null

const statusLabel: Record<string, string> = {
  planned: '📋 待确认',
  running: '⏳ 执行中',
  done: '✅ 完成',
  failed: '❌ 失败',
  cancelled: '⏹ 已取消',
}

async function loadTasks() {
  try {
    const r = await apiFetch('/api/agent/tasks')
    const d = await r.json()
    if (d.ok) tasks.value = d.tasks || []
  } catch { /* ignore */ }
}

async function loadTask(id: string) {
  try {
    const r = await apiFetch(`/api/agent/tasks/${id}`)
    const d = await r.json()
    if (d.ok) {
      current.value = d.task
      if (d.task.status === 'running') running.value = true
    }
  } catch { /* ignore */ }
}

function stepConfirm(idx: number): string {
  if (!current.value) return 'pending'
  return current.value.step_confirmations?.[String(idx)] || 'pending'
}

async function confirmStep(idx: number, allow: boolean) {
  if (!current.value) return
  try {
    const r = await apiFetch(`/api/agent/tasks/${current.value.id}/confirm-step?step_index=${idx}&allow=${allow}`, {
      method: 'POST',
    })
    const d = await r.json()
    if (d.ok) current.value = d.task
  } catch { /* ignore */ }
}

async function confirmAll(allow: boolean) {
  if (!current.value) return
  try {
    const r = await apiFetch(`/api/agent/tasks/${current.value.id}/confirm-all?allow=${allow}`, {
      method: 'POST',
    })
    const d = await r.json()
    if (d.ok) current.value = d.task
  } catch { /* ignore */ }
}

async function cancelTask() {
  if (!current.value) return
  try {
    const r = await apiFetch(`/api/agent/tasks/${current.value.id}/cancel`, { method: 'POST' })
    const d = await r.json()
    if (d.ok) {
      current.value = d.task
      running.value = false
      cancelStream()
      msg.value = '⏹ 已请求取消任务'
      await loadTasks()
    }
  } catch {
    msg.value = '✗ 取消失败'
  }
}

async function createTask() {
  const text = objective.value.trim()
  if (!text || busy.value) return
  busy.value = true
  msg.value = ''
  try {
    const body = new URLSearchParams()
    body.set('objective', text)
    body.set('user_id', 'assistant-main')
    const r = await apiFetch('/api/agent/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    })
    const d = await r.json()
    if (!r.ok || !d.ok) throw new Error(d.error || '创建失败')
    objective.value = ''
    await loadTasks()
    await loadTask(d.task.id)
    msg.value = '✓ 计划已生成，请确认要执行的步骤'
  } catch (e: unknown) {
    msg.value = '✗ ' + ((e as Error).message || e)
  } finally {
    busy.value = false
  }
}

async function runTask() {
  if (!current.value) return
  // 步骤门禁前置检查：没有允许执行的步骤时直接提示，避免"点运行没反应"
  const allowed = Object.values(current.value.step_confirmations || {}).some(v => v === 'allowed')
  if (!allowed) {
    msg.value = '请先允许至少一个步骤（允许 / 全部允许）再运行'
    return
  }
  msg.value = ''
  try {
    const r = await apiFetch(`/api/agent/tasks/${current.value.id}/run`, { method: 'POST' })
    const d = await r.json()
    if (!r.ok || !d.ok) throw new Error(d.error || '启动失败')
    running.value = true
    msg.value = '⏳ 已启动执行，正在等待步骤确认…'
    // 打开 SSE 通道接收确认请求与进度
    startStream()
  } catch (e: unknown) {
    running.value = false
    msg.value = '✗ ' + ((e as Error).message || e)
  }
}

async function respondConfirm(requestId: string, allow: boolean) {
  try {
    const r = await apiFetch('/api/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ request_id: requestId, allow: String(allow) }).toString(),
    })
    const d = await r.json()
    if (d.ok) pendingConfirms.value = pendingConfirms.value.filter(p => p.request_id !== requestId)
    else pendingConfirms.value = pendingConfirms.value.filter(p => p.request_id !== requestId)
  } catch {
    pendingConfirms.value = pendingConfirms.value.filter(p => p.request_id !== requestId)
  }
}

function startStream() {
  if (!current.value || streamCtrl) return
  const ctrl = new AbortController()
  streamCtrl = ctrl
  const base = getBaseUrl()
  fetch(`${base}/api/agent/tasks/${current.value.id}/stream`, { signal: ctrl.signal })
    .then(res => {
      if (!res.body) return
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      function pump(): Promise<void> {
        return reader.read().then(({ done, value }) => {
          if (done) return
          const chunk = decoder.decode(value, { stream: true })
          for (const part of chunk.split('\n\n')) {
            if (!part.startsWith('data: ')) continue
            try {
              const ev = JSON.parse(part.slice(6))
              if (ev.type === 'confirm_request') {
                // 工具级确认：推给确认卡片，用户批准后 POST /api/confirm
                pendingConfirms.value.push(ev)
              } else if (ev.type === 'task_done') {
                running.value = false
                pendingConfirms.value = []
                loadTask(current.value!.id)
              } else if (ev.type === 'progress' || ev.type === 'step') {
                loadTask(current.value!.id)
              }
            } catch { /* ignore */ }
          }
          return pump()
        })
      }
      return pump()
    })
    .catch(() => {})
    .finally(() => { streamCtrl = null })
}

function cancelStream() {
  streamCtrl?.abort()
  streamCtrl = null
  pendingConfirms.value = []
}

async function open() {
  await loadTasks()
  current.value = null
  msg.value = ''
}

watch(() => props.show, (v) => {
  if (v) open()
  else cancelStream()
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
  <Teleport to="body">
    <div class="overlay" :class="{ show }" @click.self="emit('close')">
      <div class="agent-panel">
        <div class="a-head">
          <div class="a-head-left">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            <span>任务代理</span>
          </div>
          <button class="a-x" @click="emit('close')">✕</button>
        </div>
        <div class="a-body">
          <!-- 新建任务 -->
          <div class="a-create">
            <input v-model="objective" type="text" placeholder="输入任务目标，例如：帮我打开记事本写个文件并截图" class="a-input" @keyup.enter="createTask" />
            <button class="a-btn" :disabled="busy || !objective.trim()" @click="createTask">生成计划</button>
          </div>

          <!-- 任务列表 -->
          <div class="a-list">
            <div v-if="tasks.length === 0" class="a-empty">暂无任务。输入目标，让菟菚拆解计划。</div>
            <div
              v-for="t in tasks"
              :key="t.id"
              class="a-item"
              :class="{ active: current && current.id === t.id }"
              @click="loadTask(t.id)"
            >
              <span class="a-item-status">{{ statusLabel[t.status] || t.status }}</span>
              <span class="a-item-title">{{ t.objective }}</span>
            </div>
          </div>

          <!-- 工具级确认卡片（任务执行中弹出） -->
          <div v-if="pendingConfirms.length > 0" class="a-confirms">
            <div v-for="pr in pendingConfirms" :key="pr.request_id" class="a-confirm-card">
              <div class="ac-head">
                <span class="ac-danger" :style="{ background: dangerColor(pr.danger) }">{{ dangerLabel(pr.danger) }}</span>
                <span class="ac-tool"><code>{{ pr.tool }}</code></span>
              </div>
              <div class="ac-msg">{{ pr.message }}</div>
              <div class="ac-args" v-if="Object.keys(pr.args).length > 0">
                <div v-for="(v, k) in pr.args" :key="k" class="ac-arg"><span class="ac-k">{{ k }}:</span><span class="ac-v">{{ v }}</span></div>
              </div>
              <div class="ac-actions">
                <button class="ac-btn reject" @click="respondConfirm(pr.request_id, false)">拒绝</button>
                <button class="ac-btn allow" @click="respondConfirm(pr.request_id, true)">允许</button>
              </div>
            </div>
          </div>

          <!-- 任务详情 + 计划确认 -->
          <div v-if="current" class="a-detail">
            <div class="a-detail-head">
              <span class="a-status">{{ statusLabel[current.status] || current.status }}</span>
              <span class="a-obj">{{ current.objective }}</span>
            </div>
            <div class="a-plan">
              <div v-for="(step, idx) in current.plan" :key="idx" class="a-step" :class="[stepConfirm(idx), step.status]">
                <div class="a-step-head">
                  <span class="a-step-idx">{{ idx + 1 }}</span>
                  <span class="a-step-title">{{ step.title }}</span>
                  <span class="a-step-cfg" v-if="stepConfirm(idx) === 'allowed'">✅ 已允许</span>
                  <span class="a-step-cfg" v-else-if="stepConfirm(idx) === 'denied'">🚫 已拒绝</span>
                  <span class="a-step-cfg" v-else>⏳ 待确认</span>
                  <span class="a-step-status" v-if="step.status === 'done'">✓ 完成</span>
                  <span class="a-step-status" v-else-if="step.status === 'running'">… 执行中</span>
                </div>
                <div class="a-step-detail" v-if="step.detail">{{ step.detail }}</div>
                <div class="a-step-result" v-if="step.result">{{ step.result }}</div>
                <div class="a-step-actions" v-if="current.status === 'planned' || current.status === 'running'">
                  <button class="step-btn reject" @click="confirmStep(idx, false)">拒绝</button>
                  <button class="step-btn allow" @click="confirmStep(idx, true)">允许</button>
                </div>
              </div>
            </div>
            <div class="a-detail-actions">
              <button v-if="current.status === 'planned'" class="a-btn ghost" @click="confirmAll(false)">全部拒绝</button>
              <button v-if="current.status === 'planned'" class="a-btn ghost" @click="confirmAll(true)">全部允许</button>
              <button v-if="current.status === 'planned'" class="a-btn run" :disabled="running" @click="runTask">▶ 开始执行</button>
              <button v-if="running" class="a-btn ghost cancel" @click="cancelTask">⏹ 取消任务</button>
              <span v-if="running" class="a-running">执行中…</span>
            </div>
            <div v-if="current.result" class="a-result">{{ current.result }}</div>
          </div>
          <div v-if="msg" class="a-msg" :class="{ err: msg.startsWith('✗') }">{{ msg }}</div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 35, 30, 0.55);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  z-index: 100;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.overlay.show { display: flex; }
.agent-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  width: min(680px, 96vw);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  animation: popIn 0.25s ease both;
}
.a-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.a-head-left { display: flex; align-items: center; gap: 8px; font-size: 1.05rem; font-weight: 700; color: var(--text); }
.a-head-left svg { color: var(--primary); }
.a-x { background: none; border: none; color: var(--text-faint); font-size: 1.1rem; cursor: pointer; padding: 2px 8px; border-radius: 6px; }
.a-x:hover { color: var(--danger); background: var(--danger-soft); }
.a-body { flex: 1; overflow-y: auto; padding: 16px 20px 20px; }
.a-create { display: flex; gap: 8px; margin-bottom: 14px; }
.a-input { flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 14px; color: var(--text); font-size: 0.88rem; outline: none; min-width: 0; }
.a-input:focus { border-color: var(--primary); box-shadow: var(--glow); }
.a-btn { background: var(--bg-user); color: #fff; border: none; border-radius: var(--radius-sm); padding: 0 20px; font-size: 0.86rem; cursor: pointer; font-weight: 600; white-space: nowrap; transition: all 0.15s; }
.a-btn:hover { background: var(--bg-user-deep); }
.a-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.a-btn.ghost { background: transparent; color: var(--text-dim); border: 1px solid var(--border); }
.a-btn.ghost:hover { color: var(--primary-deep); border-color: var(--primary); }
.a-btn.run { background: var(--primary); }
.a-btn.cancel { background: var(--danger-soft); color: var(--danger); }
.a-btn.cancel:hover { background: #f7e2d8; color: var(--danger); border-color: var(--danger); }
.a-list { display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px; }
.a-empty { font-size: 0.82rem; color: var(--text-faint); padding: 8px 0; }
.a-item { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: var(--radius-sm); cursor: pointer; background: var(--bg); border: 1px solid transparent; transition: all 0.15s; }
.a-item:hover { border-color: var(--border-light); }
.a-item.active { border-color: var(--primary); background: var(--primary-soft); }
.a-item-status { font-size: 0.7rem; color: var(--primary-deep); background: var(--primary-soft); padding: 1px 8px; border-radius: 10px; white-space: nowrap; }
.a-item-title { font-size: 0.85rem; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.a-detail { border: 1px solid var(--border); border-radius: var(--radius-md); padding: 12px 14px; background: var(--bg); }
.a-detail-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.a-status { font-size: 0.75rem; font-weight: 700; color: var(--primary-deep); }
.a-obj { font-size: 0.9rem; color: var(--text); }
.a-plan { display: flex; flex-direction: column; gap: 8px; }
.a-step { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 10px; background: #fff; }
.a-step.allowed { border-left: 3px solid #7fbf7f; }
.a-step.denied { border-left: 3px solid var(--danger); opacity: 0.7; }
.a-step.running { border-left: 3px solid var(--primary); }
.a-step.done { border-left: 3px solid var(--primary); }
.a-step-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.a-step-idx { width: 20px; height: 20px; border-radius: 50%; background: var(--primary-soft); color: var(--primary-deep); font-size: 0.72rem; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; }
.a-step-title { font-size: 0.88rem; font-weight: 600; color: var(--text); }
.a-step-cfg { font-size: 0.7rem; color: var(--text-dim); margin-left: auto; }
.a-step-status { font-size: 0.7rem; color: var(--primary); }
.a-step-detail { font-size: 0.78rem; color: var(--text-dim); margin: 4px 0 2px 28px; }
.a-step-result { font-size: 0.76rem; color: var(--text-dim); margin: 2px 0 2px 28px; white-space: pre-wrap; max-height: 60px; overflow: hidden; }
.a-step-actions { display: flex; gap: 6px; margin-top: 6px; margin-left: 28px; }
.step-btn { border: none; border-radius: 8px; padding: 4px 14px; font-size: 0.75rem; font-weight: 600; cursor: pointer; }
.step-btn.reject { background: #f5ede8; color: #8a6050; }
.step-btn.allow { background: #e6f4ea; color: #2e7d52; }
.a-detail-actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; align-items: center; }
.a-running { font-size: 0.8rem; color: var(--primary-deep); }
.a-result { margin-top: 12px; font-size: 0.84rem; color: var(--text); background: var(--primary-soft); padding: 10px 12px; border-radius: var(--radius-sm); white-space: pre-wrap; }
.a-msg { font-size: 0.8rem; margin-top: 10px; padding: 8px 12px; background: var(--primary-soft); border-radius: var(--radius-sm); color: var(--primary-deep); }
.a-msg.err { color: var(--danger); background: var(--danger-soft); }

.a-confirms { display: flex; flex-direction: column; gap: 8px; margin: 0 0 14px; }
.a-confirm-card {
  background: #fffdf5;
  border: 1px solid #e8e0c8;
  border-left: 4px solid #d9a860;
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 4px 16px rgba(40, 50, 25, 0.12);
}
.ac-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.ac-danger { font-size: 0.68rem; color: #fff; padding: 2px 8px; border-radius: 20px; font-weight: 600; }
.ac-tool { font-size: 0.8rem; color: #6a6048; }
.ac-tool code { background: #f0ede4; padding: 1px 6px; border-radius: 4px; font-size: 0.78rem; }
.ac-msg { font-size: 0.85rem; color: #3a3428; margin-bottom: 6px; line-height: 1.5; }
.ac-args { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
.ac-arg { display: flex; gap: 6px; font-size: 0.76rem; }
.ac-k { color: #8a7e66; min-width: 60px; flex-shrink: 0; }
.ac-v { color: #3a3428; word-break: break-all; max-height: 44px; overflow: hidden; }
.ac-actions { display: flex; gap: 8px; justify-content: flex-end; }
.ac-btn { border: none; border-radius: 8px; padding: 6px 16px; font-size: 0.78rem; font-weight: 600; cursor: pointer; }
.ac-btn.reject { background: #f5ede8; color: #8a6050; }
.ac-btn.allow { background: #c6d680; color: #3a4428; }
</style>
