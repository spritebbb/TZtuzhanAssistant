<script setup lang="ts">
import { onMounted, ref, watch, onUnmounted } from 'vue'
import { streamChat, uploadVision } from '../api/chat'
import { getMessages, openInitiativeStream, CURRENT_SESSION_ID, type Message } from '../api/sessions'
import ToolBar from './ToolBar.vue'
import ConfirmPanel from './ConfirmPanel.vue'
import MessageBubble from './MessageBubble.vue'
import ChatInput from './ChatInput.vue'
import type { PendingRequest } from './ConfirmPanel.vue'

const props = defineProps<{ sessionId: string | null }>()
const emit = defineEmits<{
  (e: 'open-settings'): void
  (e: 'archived'): void
}>()

const messages = ref<Message[]>([])
const input = ref('')
const busy = ref(false)
const streaming = ref(false)
const chatEl = ref<HTMLDivElement | null>(null)
const currentStream = ref<string>('')

let controller: AbortController | null = null
let curSessionId = props.sessionId
let loadSeq = 0  // 加载序号：快速切换会话时丢弃过期响应，避免串会话显示

const pendingConfirm = ref<PendingRequest[]>([])

async function loadMessages(id: string | null) {
  curSessionId = id
  const seq = ++loadSeq
  messages.value = []
  if (id) {
    const msgs = await getMessages(id)
    if (seq === loadSeq) messages.value = msgs
  }
  scrollToBottom()
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  // 清空可能残留的旧确认请求
  pendingConfirm.value = []
  busy.value = true
  input.value = ''

  // 追加用户消息
  const userMsg: Message = { role: 'user', content: text, ts: Date.now() / 1000 }
  messages.value.push(userMsg)

  // 追加空的 bot 消息占位
  const botMsg: Message = { role: 'bot', content: '', ts: Date.now() / 1000 }
  messages.value.push(botMsg)

  const botIndex = messages.value.length - 1
  currentStream.value = ''
  controller = new AbortController()
  streaming.value = true
  scrollToBottom()
  // 气泡守卫：切会话/清空后 botIndex 已失效，回调直接丢弃
  const bubble = () => messages.value[botIndex]

  try {
    await streamChat(text, curSessionId, controller.signal, {
      onPiece: (piece) => {
        const b = bubble()
        if (!b) return
        currentStream.value += piece
        b.content = currentStream.value
        scrollToBottom()
      },
      onDone: (done) => {
        const b = bubble()
        if (!b) return
        b.content = done
        currentStream.value = done
        streaming.value = false
        scrollToBottom()
      },
      onError: (err) => {
        const b = bubble()
        if (!b) return
        b.content = '⚠️ ' + err
        streaming.value = false
      },
      onReset: () => {
        const b = bubble()
        if (!b) return
        currentStream.value = ''
        b.content = ''
      },
      onImageStart: () => {
        const b = bubble()
        if (!b) return
        b.content = '🎨 正在画图，稍等一下…'
      },
      onImageUrl: (url) => {
        const b = bubble()
        if (!b) return
        // 与后端持久化格式保持一致：图片挂在当前 bot 消息上（后端只存一条
        // content+image 的 bot 消息），不再额外 push 一条气泡，避免刷新后布局变化
        b.image = url
        scrollToBottom()
      },
      onConfirmRequest: (req) => {
        pendingConfirm.value.push(req)
      },
    })
  } catch (e: unknown) {
    if ((e as Error).name !== 'AbortError' && bubble()) {
      messages.value[botIndex].content = '（网络错误：' + (e as Error).message + '）'
    }
  } finally {
    busy.value = false
    streaming.value = false
    controller = null
  }
}

function stop() {
  controller?.abort()
}

function resolveConfirm(requestId: string) {
  pendingConfirm.value = pendingConfirm.value.filter(p => p.request_id !== requestId)
}

async function handleImageFile(f: File | null) {
  if (!f || busy.value) return
  busy.value = true
  const userMsg: Message = { role: 'user', content: '（发送了一张图片）', ts: Date.now() / 1000 }
  messages.value.push(userMsg)
  const botMsg: Message = { role: 'bot', content: '', ts: Date.now() / 1000 }
  messages.value.push(botMsg)
  const botIndex = messages.value.length - 1
  // 与 send() 共用同一控制器：stop() 可取消、切会话 watch 也会中断本流
  const ctrl = new AbortController()
  controller = ctrl
  streaming.value = true
  // 气泡守卫：切会话/清空后 botIndex 已失效，回调直接丢弃，避免写 undefined
  const bubble = () => messages.value[botIndex]
  try {
    const desc = await uploadVision(f)
    if (!bubble()) return
    const text = '（我发了一张图片，图的内容是：' + desc + '）'
    await streamChat(text, curSessionId, ctrl.signal, {
      onPiece: (piece) => {
        const b = bubble()
        if (!b) return
        b.content += piece
        scrollToBottom()
      },
      onDone: (done) => {
        const b = bubble()
        if (!b) return
        b.content = done
        streaming.value = false
        scrollToBottom()
      },
      onError: (err) => {
        const b = bubble()
        if (!b) return
        b.content = '⚠️ ' + err
        streaming.value = false
      },
    })
  } catch (e: unknown) {
    if ((e as Error).name !== 'AbortError' && bubble()) {
      messages.value[botIndex].content = '⚠️ ' + (e as Error).message
    }
  } finally {
    busy.value = false
    streaming.value = false
    if (controller === ctrl) controller = null
  }
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
  })
}

watch(() => props.sessionId, (v) => {
  // 单一会话：sessionId 恒为 'current'，切到同值直接跳过，避免打断流式生成
  if (v === curSessionId) return
  if (streaming.value) {
    controller?.abort()
    streaming.value = false
  }
  pendingConfirm.value = []
  loadMessages(v)
  // 切会话后重建 SSE 长连接（订阅新会话的主动消息）
  startInitiativeStream()
  // 同步主进程的当前会话（关窗后主进程据此轮询主动消息）
  window.electronAPI?.setActiveSession(v)
})

onMounted(() => {
  loadMessages(props.sessionId)
  // 主动性 SSE 长连接：服务端后台生成主动消息时秒级推送（窗口开着时秒级）。
  startInitiativeStream()
  // 主进程轮询兜底：上报当前会话，让主进程在关窗后也能独立轮询弹系统通知。
  window.electronAPI?.setActiveSession(props.sessionId)
  // 订阅主进程转发的主动消息（追加气泡，与 SSE 去重）
  window.electronAPI?.onInitiativeMessage((text) => handleProactiveMessage(text))
})

// ---- 主动性 SSE 长连接（菟菚主动开口 + 桌面通知）----
let initiativeSource: EventSource | null = null
let notifiedKey = ''  // 已通知过的消息去重（避免重复弹同一句）

function startInitiativeStream() {
  stopInitiativeStream()
  if (!curSessionId) return
  initiativeSource = openInitiativeStream(
    curSessionId,
    (text) => handleProactiveMessage(text),
    () => {
      // SSE 断开（网络抖动/服务重启）：EventSource 会自动重连，这里仅打日志
      // 不做额外处理，避免与自动重连冲突
    },
  )
}

function stopInitiativeStream() {
  if (initiativeSource) {
    initiativeSource.close()
    initiativeSource = null
  }
}

function handleProactiveMessage(text: string) {
  if (!text || busy.value || streaming.value) return
  // 去重：同一条消息（SSE 秒级通道 + 主进程轮询兜底）可能先后各触发一次，
  // 用 notifiedKey 记录最近一条，避免重复追加气泡 + 重复弹通知。
  const dedup = text.slice(0, 40)
  if (dedup === notifiedKey) return
  notifiedKey = dedup
  // 追加为 bot 消息气泡（后端入队推送时已持久化，收到即展示）
  messages.value.push({ role: 'bot', content: text, ts: Date.now() / 1000 })
  scrollToBottom()
  // 桌面通知：仅当窗口不在前台时弹（主进程轮询在关窗时会自己弹，这里
  // 只处理「窗口开着但被遮挡/未聚焦」的场景，避免重复弹）。
  const hidden = typeof document !== 'undefined' && document.hidden
  if (hidden && window.electronAPI?.notify) {
    window.electronAPI.notify('菟菚', text).catch(() => {})
  }
}

onUnmounted(() => {
  stopInitiativeStream()
})
</script>

<template>
  <div class="chat-view">
    <ToolBar />
    <div class="chat" ref="chatEl">
      <div v-if="!messages.length" class="empty">
        <div class="empty-card">
          <!-- 菟丝花装饰 -->
          <svg viewBox="0 0 120 80" class="empty-vine" width="140" height="90">
            <path d="M10 70 Q 30 30, 50 50 T 80 20 T 110 40" fill="none" stroke="#c6d680" stroke-width="2.5" stroke-linecap="round" opacity="0.5"/>
            <path d="M50 50 Q 60 35, 55 22" fill="none" stroke="#c6d680" stroke-width="2" stroke-linecap="round" opacity="0.4"/>
            <circle cx="56" cy="20" r="5" fill="#f6e7c8" opacity="0.8"/>
            <circle cx="56" cy="20" r="3" fill="#d9a860" opacity="0.6"/>
            <circle cx="80" cy="18" r="4" fill="#f6e7c8" opacity="0.7"/>
            <circle cx="80" cy="18" r="2.5" fill="#d9a860" opacity="0.5"/>
          </svg>
          <div class="big">🌾</div>
          <div class="t">菟菚在这里。让菟丝子缠绕你的思绪，说说看？</div>
          <div class="chips">
            <span class="hint-chip" @click="input = '帮我查一下今天襄阳的天气'">⛅ 查天气</span>
            <span class="hint-chip" @click="input = '帮我画一张好看的图'">🎨 画张图</span>
            <span class="hint-chip" @click="input = '聊聊菟丝子吧'">🌿 聊菟丝子</span>
          </div>
        </div>
      </div>
      <MessageBubble
        v-for="(m, i) in messages"
        :key="i"
        :message="m"
        :is-streaming-last="streaming && i === messages.length - 1"
      />
    </div>
    <ConfirmPanel :pending="pendingConfirm" @resolve="resolveConfirm" />
    <ChatInput v-model:input="input" :busy="busy" :streaming="streaming" @send="send" @stop="stop" @file="handleImageFile" />
  </div>
</template>

<style scoped>
.chat-view {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  position: relative;
}
.chat {
  flex: 1;
  overflow-y: auto;
  padding: 22px 26px 12px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.empty-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  background: rgba(255, 253, 248, 0.7);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 40px 48px;
  box-shadow: var(--shadow-md);
  backdrop-filter: blur(8px);
  max-width: 480px;
  text-align: center;
  animation: popIn 0.3s ease both;
}
.empty-vine {
  margin-bottom: 4px;
  filter: drop-shadow(0 2px 6px rgba(164, 184, 92, 0.2));
}
.empty .big {
  font-size: 2.8rem;
  animation: breathe 3s ease-in-out infinite;
  filter: drop-shadow(0 4px 12px rgba(217, 168, 96, 0.3));
}
.empty .t { font-size: 0.95rem; color: var(--text-dim); line-height: 1.6; }
.chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 6px;
}
.hint-chip {
  font-size: 0.78rem;
  padding: 7px 14px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text-dim);
  cursor: pointer;
  transition: all 0.18s ease;
}
.hint-chip:hover {
  border-color: var(--primary);
  color: var(--primary-deep);
  background: var(--primary-soft);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

@media (max-width: 720px) {
  .chat { padding: 16px 12px 8px; }
}
</style>
