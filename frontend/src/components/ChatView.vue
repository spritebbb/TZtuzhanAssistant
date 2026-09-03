<script setup lang="ts">
import { onMounted, ref, watch, onUnmounted, computed } from 'vue'
import { streamChat, uploadVision, type ToolProgressEvent } from '../api/chat'
import { getMessages, openInitiativeStream, CURRENT_SESSION_ID, type Message } from '../api/sessions'
import { ensureBaseUrl, getApiUrl, apiFetch } from '../api'
import ToolBar from './ToolBar.vue'
import ConfirmPanel from './ConfirmPanel.vue'
import MessageBubble from './MessageBubble.vue'
import ChatInput from './ChatInput.vue'
import Portrait from './Portrait.vue'
import type { PendingRequest } from './ConfirmPanel.vue'

const props = defineProps<{ sessionId: string | null; reloadKey?: number }>()
const emit = defineEmits<{
  (e: 'open-settings'): void
  (e: 'archived'): void
  (e: 'request-archive'): void
  (e: 'streaming-change', value: boolean): void
}>()

// 全身立绘背景地址（透明抠图，干净浮在右侧）
const fullBg = ref('')
async function loadFullBg() {
  await ensureBaseUrl()
  fullBg.value = getApiUrl('/persona/full', true)
}

// 久别问候：挂载时问后端「是否久别归来」，返回问候语则作为 bot 消息即时展示。
// 后端已持久化该问候到会话历史，这里只负责当场显示，刷新后仍能读到。
async function checkGreeting(sessionId: string | null) {
  if (!sessionId) return
  try {
    const r = await apiFetch(`/api/greeting?session_id=${encodeURIComponent(sessionId)}`)
    const d = await r.json()
    if (d.ok && d.greeting) {
      messages.value.push({ role: 'bot', content: d.greeting, ts: Date.now() / 1000 })
      scrollToBottom()
    }
  } catch { /* 问候失败不影响主流程 */ }
}

const messages = ref<Message[]>([])
const input = ref('')
const busy = ref(false)
const streaming = ref(false)
const chatEl = ref<HTMLDivElement | null>(null)
const currentStream = ref<string>('')
// 工具循环进度：后端推送「正在思考/调用 XX 工具」，在流式气泡上实时展示（避免空窗）
const toolStatus = ref('')

// 消息较多时立绘降为极淡，避免压住内容
const hasManyMessages = computed(() => messages.value.length > 8)

// 归档提示条：消息数达到阈值时，在对话上方轻量提示可归档
const dismissArchiveHint = ref(false)
const showArchiveHint = computed(() => !dismissArchiveHint.value && messages.value.length >= 40)

// 当前活跃流的 AbortController（send 与 handleImageFile 共用同一引用，
// stop()/切会话统一 abort 它）。每个流在 finally 用「controller === ctrl」
// 守卫判断是否由自己清理，避免一个流结束时误清掉另一个流的引用。
let controller: AbortController | null = null
let currentRequestId: string | null = null
let curSessionId = props.sessionId
let loadSeq = 0  // 加载序号：快速切换会话时丢弃过期响应，避免串会话显示

const pendingConfirm = ref<PendingRequest[]>([])

// === 对话内搜索（Ctrl+F） ===
const searchOpen = ref(false)
const searchQuery = ref('')
const searchMatches = ref<{ index: number; count: number }[]>([])
const activeMatch = ref(-1)
const messageEls = ref<HTMLElement[]>([])

function openSearch() { searchOpen.value = true; searchQuery.value = ''; searchMatches.value = []; activeMatch.value = -1 }
function closeSearch() { searchOpen.value = false; searchQuery.value = ''; searchMatches.value = []; activeMatch.value = -1 }

function runSearch() {
  const q = searchQuery.value.trim().toLowerCase()
  searchMatches.value = []
  activeMatch.value = -1
  if (!q) return
  messages.value.forEach((m, i) => {
    const content = (m.content || '') + (m.image ? ' [图片]' : '')
    const count = content.toLowerCase().split(q).length - 1
    if (count > 0) searchMatches.value.push({ index: i, count })
  })
  if (searchMatches.value.length) jumpMatch(1)
}
function jumpMatch(dir: 1 | -1) {
  if (!searchMatches.value.length) return
  const next = (activeMatch.value + dir + searchMatches.value.length) % searchMatches.value.length
  activeMatch.value = next
  const idx = searchMatches.value[next].index
  // 滚动到对应消息
  requestAnimationFrame(() => {
    const el = messageEls.value[idx]
    if (el && chatEl.value) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.remove('hl-flash')
      void el.offsetWidth
      el.classList.add('hl-flash')
    }
  })
}
const matchSummary = computed(() => searchMatches.value.length ? `${activeMatch.value + 1}/${searchMatches.value.length}` : '0/0')

// 上报生成状态给父组件：流式生成中（busy 或 streaming）时父组件禁用归档，
// 避免「流式中归档」把对话拆成两半（后端 _runner 客户端断开后仍会继续落库到已清空的新会话）。
const isGenerating = computed(() => busy.value || streaming.value)
watch(isGenerating, (v) => emit('streaming-change', v))

async function loadMessages(id: string | null) {
  curSessionId = id
  const seq = ++loadSeq
  messages.value = []
  if (id) {
    const msgs = await getMessages(id)
    if (seq === loadSeq) messages.value = msgs
  }
  closeSearch()
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
  toolStatus.value = ''
  const ctrl = new AbortController()
  const requestId = crypto.randomUUID()
  controller = ctrl
  currentRequestId = requestId
  streaming.value = true
  scrollToBottom()
  // 气泡守卫：切会话/清空后 botIndex 已失效，回调直接丢弃
  const bubble = () => messages.value[botIndex]

  try {
    await streamChat(text, curSessionId, ctrl.signal, {
      onPiece: (piece) => {
        const b = bubble()
        if (!b) return
        currentStream.value += piece
        b.content = currentStream.value
        scrollToBottom()
      },
      onTool: handleToolEvent,
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
    }, null, requestId)
  } catch (e: unknown) {
    if ((e as Error).name === 'AbortError' && bubble() && !messages.value[botIndex].content) {
      messages.value[botIndex].content = '（已停止）'
    } else if ((e as Error).name !== 'AbortError' && bubble()) {
      messages.value[botIndex].content = '（网络错误：' + (e as Error).message + '）'
    }
  } finally {
    busy.value = false
    streaming.value = false
    toolStatus.value = ''
    if (controller === ctrl) controller = null
    if (currentRequestId === requestId) currentRequestId = null
    flushPendingProactive()
  }
}

function stop() {
  const requestId = currentRequestId
  if (requestId) void apiFetch(`/api/chat/${encodeURIComponent(requestId)}/cancel`, { method: 'POST' })
  controller?.abort()
}

// 工具名 → 中文描述（进度展示用）
const toolLabels: Record<string, string> = {
  web_search: '正在联网搜索…',
  web_fetch: '正在抓取网页…',
  memory_search: '正在回想记忆…',
  memory_add: '正在记下来…',
  todo_create: '正在记待办…',
  run_python: '正在跑代码…',
  run_command: '正在执行命令…',
  read_file: '正在读文件…',
  write_file: '正在写文件…',
  glob: '正在找文件…',
  grep: '正在搜代码…',
  agent_run: '正在派子代理…',
  skill_load: '正在加载技能…',
  generate_image: '正在画图…',
}

function handleToolEvent(ev: ToolProgressEvent) {
  if (ev.type === 'thinking') {
    toolStatus.value = '正在思考…'
  } else if (ev.type === 'tool') {
    toolStatus.value = toolLabels[ev.name || ''] || `正在调用 ${ev.name}…`
  } else if (ev.type === 'tool_done') {
    // 工具完成后回到「整理回复」状态
    toolStatus.value = '正在整理回复…'
  }
}

function resolveConfirm(requestId: string) {
  pendingConfirm.value = pendingConfirm.value.filter(p => p.request_id !== requestId)
}

async function handleImageFile(f: File | null) {
  if (!f || busy.value) return
  busy.value = true
  // 占位：先放一张空 user 气泡，识图结果返回后再填充完整文案，
  // 保证「当场显示」与「后端落库」用的是同一句文案（刷新/归档后一致，不产生错位）。
  const userMsg: Message = { role: 'user', content: '（发送了一张图片）', ts: Date.now() / 1000 }
  messages.value.push(userMsg)
  const botMsg: Message = { role: 'bot', content: '', ts: Date.now() / 1000 }
  messages.value.push(botMsg)
  const botIndex = messages.value.length - 1
  // 每流独立 AbortController，但统一挂到 controller 供 stop()/切会话 abort；
  // finally 用「controller === ctrl」守卫，避免本流结束时误清其他流的引用。
  const ctrl = new AbortController()
  const requestId = crypto.randomUUID()
  controller = ctrl
  currentRequestId = requestId
  streaming.value = true
  toolStatus.value = ''
  // 气泡守卫：切会话/清空后 botIndex 已失效，回调直接丢弃，避免写 undefined
  const bubble = () => messages.value[botIndex]
  try {
    const { description: desc, imageUrl } = await uploadVision(f)
    if (!bubble()) return
    // 识图成功：把图片挂到 user 消息上（缩略图），文案与后端持久化一致。
    // 后端 /api/chat 会把这个 image 随 user 消息一起落库，刷新/归档后原图仍在。
    if (imageUrl) {
      userMsg.image = imageUrl
    }
    // 当场展示的 user 文案 = 后端落库的文案，二者完全一致，刷新后不会从短句变成长描述
    const text = '（我发了一张图片，图的内容是：' + desc + '）'
    userMsg.content = text
    // 用整体替换强制触发重渲染：否则改的是 push 进去的原始对象引用，
    // 要等 bot 第一条流式片段到达才会"刷"成完整描述，识图+LLM 首字的几秒里
    // 用户看到的仍是占位文案。
    const userIndex = messages.value.indexOf(userMsg)
    if (userIndex >= 0) {
      messages.value[userIndex] = { ...userMsg }
    }
    await streamChat(text, curSessionId, ctrl.signal, {
      onPiece: (piece) => {
        const b = bubble()
        if (!b) return
        b.content += piece
        scrollToBottom()
      },
      onTool: handleToolEvent,
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
    }, imageUrl, requestId)
  } catch (e: unknown) {
    if ((e as Error).name === 'AbortError' && bubble() && !messages.value[botIndex].content) {
      messages.value[botIndex].content = '（已停止）'
    } else if ((e as Error).name !== 'AbortError' && bubble()) {
      messages.value[botIndex].content = '⚠️ ' + (e as Error).message
    }
  } finally {
    busy.value = false
    streaming.value = false
    toolStatus.value = ''
    if (controller === ctrl) controller = null
    if (currentRequestId === requestId) currentRequestId = null
    flushPendingProactive()
  }
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
  })
}

// === 键盘快捷键 ===
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && e.shiftKey) {
    e.preventDefault()
    openSearch()
    return
  }
  if (e.key === 'Escape' && searchOpen.value) {
    closeSearch()
  }
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

// 归档成功后（父组件 reloadKey 自增）强制清空并重载当前会话
watch(() => props.reloadKey, () => {
  if (streaming.value) {
    controller?.abort()
    streaming.value = false
  }
  pendingConfirm.value = []
  currentStream.value = ''
  loadMessages(curSessionId)
})

let unsubscribeInitiativeIpc: (() => void) | undefined

onMounted(async () => {
  await loadMessages(props.sessionId)
  void loadFullBg()
  // 久别问候：加载完历史后检查一次，有问候语则追加展示
  await checkGreeting(props.sessionId)
  // 主动性 SSE 长连接：服务端后台生成主动消息时秒级推送（窗口开着时秒级）。
  startInitiativeStream()
  // 主进程轮询兜底：上报当前会话，让主进程在关窗后也能独立轮询弹系统通知。
  window.electronAPI?.setActiveSession(props.sessionId)
  // 订阅主进程转发的主动消息（追加气泡，与 SSE 去重）
  unsubscribeInitiativeIpc = window.electronAPI?.onInitiativeMessage((text) => handleProactiveMessage(text))
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  stopInitiativeStream()
  unsubscribeInitiativeIpc?.()
  unsubscribeInitiativeIpc = undefined
  document.removeEventListener('keydown', onKeydown)
})

// ---- 主动性 SSE 长连接（菟菚主动开口 + 桌面通知）----
let initiativeSource: EventSource | null = null
// busy/streaming 期间收到的主动消息暂存，流结束后补插（避免静默丢失展示）
let pendingProactive: string[] = []

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

// 与消息列表最后一条 content 比对去重（与数据库端「最后一条 bot content 相同
// 则跳过」的幂等逻辑一致），避免组件重挂载后 notifiedKey 重置导致同一条消息
// 被 SSE 首帧 + 主进程转发推成两条相邻气泡。
function _isDuplicateProactive(text: string): boolean {
  const last = messages.value[messages.value.length - 1]
  return !!last && last.role === 'bot' && last.content === text
}

function handleProactiveMessage(text: string) {
  if (!text) return
  if (_isDuplicateProactive(text)) return
  // 生成中（busy/streaming）：不打断当前流，暂存到 pendingProactive，
  // 流结束或 busy 释放后补插气泡；消息已落库，刷新也不会丢。
  if (busy.value || streaming.value) {
    if (!pendingProactive.includes(text)) pendingProactive.push(text)
    return
  }
  // 追加为 bot 消息气泡。主动消息后端已写入会话 messages 表（落库 + 幂等），
  // 这里即时展示。
  messages.value.push({ role: 'bot', content: text, ts: Date.now() / 1000 })
  scrollToBottom()
  // 桌面通知：窗口可见时由主进程轮询弹；但若页面隐藏（窗口最小化/后台标签页），
  // SSE 通道仍会先消费消息，主进程 30s 轮询再取时就已空 → 通知丢失。因此这里
  // 在页面隐藏时主动请求一次系统通知，主进程 notify 里用 lastNotifiedText 去重，
  // 与轮询通道谁先到都只弹一次，不会双弹。
  if (document.hidden) {
    window.electronAPI?.notify('菟菚', text)
  }
}

// 流结束 / busy 释放后，若有暂存的主动消息则补插（避免静默丢失展示）
function flushPendingProactive() {
  if (pendingProactive.length && !busy.value && !streaming.value) {
    const queued = pendingProactive
    pendingProactive = []
    for (const text of queued) handleProactiveMessage(text)
  }
}

function setMessageEl(i: number, el: unknown) {
  if (el) messageEls.value[i] = el as HTMLElement
}

function isMatch(i: number): boolean {
  if (!searchQuery.value.trim()) return false
  return searchMatches.value.some(m => m.index === i)
}
function isActive(i: number): boolean {
  return searchMatches.value[activeMatch.value]?.index === i
}
</script>

<template>
  <div class="chat-view">
    <!-- 全身立绘背景（透明抠图，浮在右侧，不遮内容） -->
    <img
      v-if="fullBg"
      class="full-bg"
      :class="{ faded: hasManyMessages }"
      :src="fullBg"
      alt=""
      aria-hidden="true"
    />
    <ToolBar />
    <!-- 归档提示条：对话过长时轻量提醒，可一键归档当前对话 -->
    <div v-if="showArchiveHint" class="archive-hint">
      <span class="ah-text">这段对话已经挺长了，要不要先归档存档？</span>
      <button class="ah-btn" :disabled="isGenerating" :title="isGenerating ? '正在生成回复，暂不能归档' : ''" @click="emit('request-archive')">归档当前对话</button>
      <button class="ah-x" title="关闭" @click="dismissArchiveHint = true" v-if="!dismissArchiveHint">✕</button>
    </div>
    <!-- 对话内搜索工具条 -->
    <div v-if="searchOpen" class="searchbar glass">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
      <input
        v-model="searchQuery"
        type="text"
        placeholder="在对话中查找…"
        class="search-input"
        @input="runSearch"
        @keydown.enter="jumpMatch(1)"
        @keydown.esc="closeSearch"
      />
      <span class="match-sum">{{ matchSummary }}</span>
      <button class="search-nav" title="上一个 (Shift+Enter)" @click="jumpMatch(-1)">↑</button>
      <button class="search-nav" title="下一个 (Enter)" @click="jumpMatch(1)">↓</button>
      <button class="search-x" title="关闭 (Esc)" @click="closeSearch">✕</button>
    </div>

    <div class="chat" ref="chatEl">
      <div v-if="!messages.length" class="empty">
        <div class="empty-card glass">
          <!-- 角色卡：立绘徽章 + 藤蔓点缀 -->
          <div class="empty-portrait">
            <div class="empty-halo"></div>
            <div class="empty-avatar"><Portrait :size="108" /></div>
            <svg viewBox="0 0 160 30" class="empty-vine" width="160" height="30">
              <path d="M4 24 Q 40 10, 78 20 T 156 14" fill="none" stroke="var(--primary-light)" stroke-width="2.2" stroke-linecap="round" opacity="0.5"/>
              <circle cx="156" cy="14" r="3" fill="var(--accent-light)" opacity="0.8"/>
              <circle cx="156" cy="14" r="1.8" fill="var(--accent)" opacity="0.6"/>
              <circle cx="40" cy="10" r="2.4" fill="var(--accent-light)" opacity="0.6"/>
            </svg>
          </div>
          <div class="t">细藤缠绕 · 温润坚韧</div>
          <div class="t-sub">我是菟菚，寄生予万物，也联结着万物。说说看？</div>
          <div class="chips">
            <span class="hint-chip" @click="input = '帮我查一下今天襄阳的天气'">查天气</span>
            <span class="hint-chip" @click="input = '帮我画一张好看的图'">画张图</span>
            <span class="hint-chip" @click="input = '聊聊菟丝子吧'">聊菟丝子</span>
          </div>
        </div>
      </div>
      <MessageBubble
        v-for="(m, i) in messages"
        :key="i"
        :ref="(el: any) => setMessageEl(i, el?.$el ?? el)"
        :message="m"
        :is-streaming-last="streaming && i === messages.length - 1"
        :tool-status="streaming && i === messages.length - 1 ? toolStatus : ''"
        :search-query="searchQuery"
        :is-match="isMatch(i)"
        :is-active-match="isActive(i)"
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
  overflow: hidden;
}
/* 全身立绘背景：浮在右侧下方，透明不遮内容，边缘柔和渐隐 */
.full-bg {
  position: absolute;
  right: 46px;
  bottom: 0;
  height: 88%;
  max-height: 660px;
  width: auto;
  max-width: 320px;
  z-index: 1;
  pointer-events: none;
  object-fit: contain;
  object-position: bottom center;
  opacity: 0.92;
  -webkit-mask-image: linear-gradient(300deg, rgba(0,0,0,0.96) 0%, rgba(0,0,0,0.9) 92%, rgba(0,0,0,0) 100%);
  mask-image: linear-gradient(300deg, rgba(0,0,0,0.96) 0%, rgba(0,0,0,0.9) 92%, rgba(0,0,0,0) 100%);
  filter: drop-shadow(0 10px 34px rgba(0, 0, 0, 0.45));
  transition: opacity 0.3s ease;
}
.theme-light .full-bg { opacity: 0.95; }
.full-bg.faded { opacity: 0.4; }
.chat {
  flex: 1;
  overflow-y: auto;
  padding: 22px 26px 12px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  scroll-behavior: smooth;
  position: relative;
  z-index: 2;
}
/* 植物背景：挂在 .chat-view 底层，立绘(z1)在其上，内容(z2)最上 */
.chat-view::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  background-image:
    var(--chat-bg-overlay),
    var(--chat-bg-image);
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  pointer-events: none;
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
  gap: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 36px 48px;
  box-shadow: var(--shadow-md);
  max-width: 460px;
  text-align: center;
  animation: popIn 0.3s ease both;
}
.empty-portrait {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 8px;
  position: relative;
}
.empty-halo {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 150px;
  height: 150px;
  transform: translate(-50%, -58%);
  border-radius: 50%;
  background: radial-gradient(circle, var(--primary-soft) 0%, transparent 65%);
  filter: blur(4px);
  pointer-events: none;
}
.empty-avatar :deep(.portrait-wrap) {
  border-radius: 50%;
  border: 2px solid var(--border-light);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
  position: relative;
  overflow: hidden;
}
.empty-avatar :deep(.portrait) {
  object-fit: cover;
  object-position: center 18%;
  border-radius: 50%;
}
.empty-vine {
  margin-top: 10px;
  filter: drop-shadow(0 2px 6px rgba(139, 165, 102, 0.2));
}
.empty .t { font-size: 1.1rem; color: var(--primary-text); font-weight: 700; line-height: 1.5; letter-spacing: 0.5px; }
.empty .t-sub { font-size: 0.9rem; color: var(--text-dim); line-height: 1.6; max-width: 300px; }
.chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 8px;
}
.hint-chip {
  font-size: 0.78rem;
  padding: 7px 14px;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-dim);
  cursor: pointer;
  transition: all 0.18s ease;
  backdrop-filter: blur(8px);
}
.hint-chip:hover {
  border-color: var(--primary);
  color: var(--primary-text);
  background: var(--primary-soft);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

/* 归档提示条 */
.archive-hint {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 22px 0;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-light);
  background: var(--primary-soft);
  color: var(--primary-text);
  font-size: 0.8rem;
  flex-shrink: 0;
  z-index: 5;
  animation: fadeUp 0.25s ease both;
}
.ah-text { flex: 1; min-width: 0; }
.ah-btn {
  border: 1px solid var(--primary);
  background: var(--primary);
  color: #fff;
  padding: 4px 12px;
  border-radius: var(--radius-full);
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 600;
  white-space: nowrap;
  transition: all 0.16s ease;
}
.ah-btn:hover { filter: brightness(1.08); transform: translateY(-1px); }
.ah-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  filter: none;
  transform: none;
}
.ah-x {
  border: none;
  background: none;
  color: var(--text-dim);
  cursor: pointer;
  font-size: 0.8rem;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}
.ah-x:hover { background: var(--bg-hover); color: var(--primary-text); }

@keyframes fadeUp {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 对话内搜索条 */
.searchbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 22px 0;
  padding: 6px 10px;
  border-radius: var(--radius-md);
  flex-shrink: 0;
  color: var(--text-dim);
  z-index: 5;
}
.search-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text);
  font-size: 0.85rem;
  min-width: 0;
}
.search-input::placeholder { color: var(--text-faint); }
.match-sum { font-size: 0.72rem; color: var(--text-faint); white-space: nowrap; }
.search-nav {
  width: 26px; height: 26px;
  display: flex; align-items: center; justify-content: center;
  border: none; background: var(--bg-hover); color: var(--text-dim);
  border-radius: var(--radius-sm); cursor: pointer; font-size: 0.8rem;
  transition: all 0.15s;
}
.search-nav:hover { background: var(--primary-soft); color: var(--primary-text); }
.search-x {
  width: 26px; height: 26px;
  display: flex; align-items: center; justify-content: center;
  border: none; background: none; color: var(--text-faint);
  border-radius: var(--radius-sm); cursor: pointer; font-size: 0.8rem;
}
.search-x:hover { background: var(--danger-soft); color: var(--danger); }

@media (max-width: 768px) {
  .chat { padding: 16px 12px 8px; }
  .searchbar { margin: 6px 10px 0; }
}
</style>
