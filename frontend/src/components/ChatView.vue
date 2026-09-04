<script setup lang="ts">
import { onMounted, ref, watch, onUnmounted, computed } from 'vue'
import { streamChat, uploadVision, type ToolProgressEvent } from '../api/chat'
import { getMessages, openInitiativeStream, CURRENT_SESSION_ID, type Message, type ProactiveMessage } from '../api/sessions'
import { ensureBaseUrl, getApiUrl, apiFetch } from '../api'
import ToolBar from './ToolBar.vue'
import ConfirmPanel from './ConfirmPanel.vue'
import MessageBubble from './MessageBubble.vue'
import ChatInput from './ChatInput.vue'
import Portrait from './Portrait.vue'
import type { PendingRequest } from './ConfirmPanel.vue'
import { autoPlayTts, stopTts } from '../utils/tts'
import { portraitBondFor, portraitMoodFor, type PortraitBond, type PortraitMood } from '../utils/portrait'

const props = defineProps<{
  sessionId: string | null
  reloadKey?: number
  externalDraft?: string
  externalDraftKey?: number
}>()
const emit = defineEmits<{
  (e: 'open-settings'): void
  (e: 'archived'): void
  (e: 'request-archive'): void
  (e: 'streaming-change', value: boolean): void
}>()

// 全身立绘背景地址（透明抠图，干净浮在右侧）
const fullBg = ref('')
const chibiBg = `${import.meta.env.BASE_URL}avatars/tuzhan-chibi-v1.png`
const portraitMood = ref<PortraitMood>('lazy')
const portraitBond = ref<PortraitBond>('initial')
const portraitMoodLabel = ref('慵懒')

async function refreshPortraitState() {
  try {
    const r = await apiFetch('/api/meta')
    const d = await r.json()
    if (d.mood) {
      const nextMood = portraitMoodFor(Number(d.mood.value ?? 60))
      portraitMood.value = nextMood
      portraitMoodLabel.value = String(d.mood.label || '平淡')
      fullBg.value = getApiUrl(`/persona/full/${nextMood}?v=20260904b`, true)
    }
    if (d.affection) portraitBond.value = portraitBondFor(String(d.affection.stage || '初识'))
  } catch { /* 状态刷新失败时保留上一帧 */ }
}

async function loadFullBg() {
  await ensureBaseUrl()
  // meta 拉取失败时至少展示基础档；成功后立即切到对应心情差分。
  fullBg.value = getApiUrl('/persona/full/plain?v=20260904b', true)
  await refreshPortraitState()
}

let portraitRefreshTimer: ReturnType<typeof setTimeout> | null = null
function schedulePortraitRefresh() {
  void refreshPortraitState()
  if (portraitRefreshTimer) clearTimeout(portraitRefreshTimer)
  // 情绪感知在回复旁路中结算，稍后再读一次以捕获刚落账的状态。
  portraitRefreshTimer = setTimeout(() => { void refreshPortraitState() }, 1800)
}

// 久别问候：挂载时问后端「是否久别归来」，返回问候语则作为 bot 消息即时展示。
// 后端已持久化该问候到会话历史，这里只负责当场显示，刷新后仍能读到。
async function checkGreeting(sessionId: string | null) {
  if (!sessionId) return
  try {
    const r = await apiFetch(`/api/greeting?session_id=${encodeURIComponent(sessionId)}`)
    const d = await r.json()
    if (d.ok && d.greeting) {
      const message: Message = { role: 'bot', content: d.greeting, ts: Date.now() / 1000 }
      messages.value.push(message)
      autoPlayTts(message.content, ttsKey(message, messages.value.length - 1))
      scrollToBottom()
    }
  } catch { /* 问候失败不影响主流程 */ }
}

const messages = ref<Message[]>([])
const input = ref('')
const busy = ref(false)
const initializing = ref(true)
const streaming = ref(false)
const chatEl = ref<HTMLDivElement | null>(null)
const currentStream = ref<string>('')
// 工具循环进度：后端推送「正在思考/调用 XX 工具」，在流式气泡上实时展示（避免空窗）
const toolStatus = ref('')

// 共同活动只把讨论话题带回输入框，由用户自己确认发送。
watch(() => props.externalDraftKey, () => {
  const draft = props.externalDraft?.trim()
  if (!draft) return
  input.value = input.value.trim() ? `${input.value.trim()}\n${draft}` : draft
})

function ttsKey(message: Message, index: number): string {
  return `${message.ts}:${index}`
}

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
  if (!text || busy.value || initializing.value) return
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
        autoPlayTts(done, ttsKey(b, botIndex))
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
      onExplanation: (value) => {
        const b = bubble()
        if (b) b.explanation = value
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
    schedulePortraitRefresh()
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
  if (!f || busy.value || initializing.value) return
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
        autoPlayTts(done, ttsKey(b, botIndex))
        scrollToBottom()
      },
      onError: (err) => {
        const b = bubble()
        if (!b) return
        b.content = '⚠️ ' + err
        streaming.value = false
      },
      onExplanation: (value) => {
        const b = bubble()
        if (b) b.explanation = value
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
    schedulePortraitRefresh()
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
  try {
    await loadMessages(props.sessionId)
  } finally {
    // 历史加载前不允许发消息，避免极速输入被稍后返回的空历史覆盖。
    initializing.value = false
  }
  void loadFullBg()
  // 久别问候可以慢慢生成，不阻塞用户输入；后端会在生成完成时检查会话是否
  // 已经活跃，若用户先开聊就丢弃过时问候。
  void checkGreeting(props.sessionId)
  // 主动性 SSE 长连接：服务端后台生成主动消息时秒级推送（窗口开着时秒级）。
  startInitiativeStream()
  // 主进程轮询兜底：上报当前会话，让主进程在关窗后也能独立轮询弹系统通知。
  window.electronAPI?.setActiveSession(props.sessionId)
  // 订阅主进程转发的主动消息（追加气泡，与 SSE 去重）
  unsubscribeInitiativeIpc = window.electronAPI?.onInitiativeMessage((message) => handleProactiveMessage(message))
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  stopTts()
  stopInitiativeStream()
  unsubscribeInitiativeIpc?.()
  unsubscribeInitiativeIpc = undefined
  document.removeEventListener('keydown', onKeydown)
  if (portraitRefreshTimer) clearTimeout(portraitRefreshTimer)
})

// ---- 主动性 SSE 长连接（菟菚主动开口 + 桌面通知）----
let initiativeSource: EventSource | null = null
// busy/streaming 期间收到的主动消息暂存，流结束后补插（避免静默丢失展示）
let pendingProactive: ProactiveMessage[] = []

function startInitiativeStream() {
  stopInitiativeStream()
  if (!curSessionId) return
  initiativeSource = openInitiativeStream(
    curSessionId,
    (message) => handleProactiveMessage(message),
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
function _isDuplicateProactive(message: ProactiveMessage): boolean {
  const last = messages.value[messages.value.length - 1]
  return !!last
    && last.role === 'bot'
    && last.content === message.text
    && (last.image ?? null) === (message.image ?? null)
}

function handleProactiveMessage(input: ProactiveMessage | string) {
  const message: ProactiveMessage = typeof input === 'string'
    ? { text: input, image: null }
    : { text: input.text, image: input.image ?? null }
  if (!message.text) return
  if (_isDuplicateProactive(message)) return
  // 生成中（busy/streaming）：不打断当前流，暂存到 pendingProactive，
  // 流结束或 busy 释放后补插气泡；消息已落库，刷新也不会丢。
  if (busy.value || streaming.value) {
    if (!pendingProactive.some(item => item.text === message.text && (item.image ?? null) === (message.image ?? null))) {
      pendingProactive.push(message)
    }
    return
  }
  // 追加为 bot 消息气泡。主动消息后端已写入会话 messages 表（落库 + 幂等），
  // 这里即时展示。
  const botMessage: Message = {
    role: 'bot',
    content: message.text,
    image: message.image ?? null,
    ts: Date.now() / 1000,
  }
  messages.value.push(botMessage)
  autoPlayTts(botMessage.content, ttsKey(botMessage, messages.value.length - 1))
  scrollToBottom()
  // 桌面通知：窗口可见时由主进程轮询弹；但若页面隐藏（窗口最小化/后台标签页），
  // SSE 通道仍会先消费消息，主进程 30s 轮询再取时就已空 → 通知丢失。因此这里
  // 在页面隐藏时主动请求一次系统通知，主进程 notify 里用 lastNotifiedText 去重，
  // 与轮询通道谁先到都只弹一次，不会双弹。
  if (document.hidden) {
    window.electronAPI?.notify('菟菚', message.text)
  }
}

// 流结束 / busy 释放后，若有暂存的主动消息则补插（避免静默丢失展示）
function flushPendingProactive() {
  if (pendingProactive.length && !busy.value && !streaming.value) {
    const queued = pendingProactive
    pendingProactive = []
    for (const message of queued) handleProactiveMessage(message)
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
  <div class="chat-view" :class="{ 'has-messages': messages.length > 0 }">
    <!-- 全身立绘背景：月光氛围层 + 透明抠图，让角色自然融入场景 -->
    <div
      v-if="fullBg"
      class="portrait-stage"
      :class="[
        { faded: hasManyMessages, 'idle-portrait': !messages.length },
        `mood-${portraitMood}`,
        `bond-${portraitBond}`,
      ]"
      :data-mood="portraitMoodLabel"
      aria-hidden="true"
    >
      <div class="portrait-moon"></div>
      <img class="full-bg" :src="fullBg" alt="" />
      <span class="portrait-state-mark">{{ portraitMoodLabel }}</span>
    </div>
    <!-- 左侧 Q 版陪伴层：和右侧立绘构成平衡，但保持为低优先级背景。 -->
    <div class="chibi-stage" :class="{ faded: hasManyMessages }" aria-hidden="true">
      <div class="chibi-glow"></div>
      <img class="chibi-bg" :src="chibiBg" alt="" />
    </div>
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
            <span class="empty-orbit orbit-a"></span>
            <span class="empty-orbit orbit-b"></span>
            <div class="empty-avatar"><Portrait :size="108" /></div>
            <svg viewBox="0 0 160 30" class="empty-vine" width="160" height="30">
              <path d="M4 24 Q 40 10, 78 20 T 156 14" fill="none" stroke="var(--primary-light)" stroke-width="2.2" stroke-linecap="round" opacity="0.5"/>
              <circle cx="156" cy="14" r="3" fill="var(--accent-light)" opacity="0.8"/>
              <circle cx="156" cy="14" r="1.8" fill="var(--accent)" opacity="0.6"/>
              <circle cx="40" cy="10" r="2.4" fill="var(--accent-light)" opacity="0.6"/>
            </svg>
          </div>
          <div class="eyebrow"><span></span>此刻也在这里</div>
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
        :tts-key="ttsKey(m, i)"
        :search-query="searchQuery"
        :is-match="isMatch(i)"
        :is-active-match="isActive(i)"
      />
    </div>
    <ConfirmPanel :pending="pendingConfirm" @resolve="resolveConfirm" />
    <ChatInput v-model:input="input" :busy="busy || initializing" :streaming="streaming" @send="send" @stop="stop" @file="handleImageFile" />
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
/* 全身立绘背景：舞台层统一月光、景深与底部融入，避免贴图感 */
.portrait-stage {
  position: absolute;
  right: clamp(22px, 4.6vw, 76px);
  bottom: -22px;
  height: min(82%, 620px);
  width: min(34vw, 370px);
  z-index: 1;
  pointer-events: none;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  opacity: 0.70;
  transition: opacity 0.45s ease, transform 0.45s ease, filter 0.45s ease;
}
.portrait-stage::before {
  content: '';
  position: absolute;
  z-index: 0;
  width: 118%;
  height: 62%;
  bottom: 4%;
  border-radius: 50%;
  background: radial-gradient(ellipse, rgba(223, 183, 140, 0.16) 0%, rgba(172, 135, 199, 0.10) 34%, transparent 70%);
  filter: blur(22px);
  transform: translateX(4%);
}
.portrait-stage::after {
  content: '';
  position: absolute;
  z-index: 2;
  inset: auto -4% 0 -4%;
  height: 24%;
  pointer-events: none;
  background: linear-gradient(to top, rgba(19, 18, 28, 0.34), transparent);
}
.portrait-moon {
  position: absolute;
  z-index: 0;
  top: 5%;
  right: 8%;
  width: 46%;
  aspect-ratio: 1;
  border-radius: 50%;
  background: radial-gradient(circle at 42% 38%, rgba(255, 249, 225, 0.42), rgba(233, 207, 183, 0.15) 34%, rgba(187, 143, 204, 0.05) 56%, transparent 72%);
  filter: blur(6px);
}
.full-bg {
  position: relative;
  z-index: 1;
  display: block;
  width: auto;
  max-width: 100%;
  height: 100%;
  pointer-events: none;
  object-fit: contain;
  object-position: bottom center;
  opacity: 0.88;
  -webkit-mask-image: linear-gradient(to top, transparent 0%, rgba(0,0,0,0.72) 8%, #000 20%, #000 100%);
  mask-image: linear-gradient(to top, transparent 0%, rgba(0,0,0,0.72) 8%, #000 20%, #000 100%);
  filter: saturate(0.84) brightness(0.86) contrast(0.96) sepia(0.05) drop-shadow(0 18px 28px rgba(4, 4, 10, 0.36));
  transform: translateY(2px) rotate(-0.7deg);
  transform-origin: bottom center;
  transition: filter 0.45s ease, transform 0.45s ease;
}
.portrait-stage:hover .full-bg { transform: translateY(-2px) rotate(-0.25deg); }
.theme-light .portrait-stage { opacity: 0.74; }
.theme-light .portrait-stage.idle-portrait {
  opacity: 0.25;
  transform: translateX(20px) scale(0.96);
  filter: saturate(0.78);
}
.theme-light .portrait-stage::before {
  background: radial-gradient(ellipse, rgba(255, 219, 143, 0.26) 0%, rgba(182, 201, 140, 0.14) 40%, transparent 72%);
}
.theme-light .portrait-stage::after { background: linear-gradient(to top, rgba(248, 243, 231, 0.74), transparent); }
.theme-light .portrait-moon {
  top: 8%;
  right: 5%;
  background: radial-gradient(circle at 42% 38%, rgba(255, 252, 213, 0.72), rgba(244, 207, 127, 0.24) 34%, rgba(184, 203, 137, 0.08) 58%, transparent 73%);
  filter: blur(8px);
}
.theme-light .full-bg {
  opacity: 0.84;
  filter: saturate(0.86) brightness(1.04) contrast(0.94) sepia(0.07) drop-shadow(0 14px 24px rgba(92, 81, 44, 0.18));
}
.portrait-state-mark {
  position: absolute;
  z-index: 3;
  right: 3%;
  bottom: 18%;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid rgba(220, 199, 157, 0.22);
  background: rgba(18, 24, 22, 0.42);
  color: rgba(239, 231, 211, 0.62);
  font-size: 0.66rem;
  letter-spacing: 0.12em;
  backdrop-filter: blur(8px);
  opacity: 0.56;
  transition: opacity 0.35s ease, background 0.35s ease;
}
.theme-light .portrait-state-mark {
  background: rgba(250, 245, 229, 0.58);
  color: rgba(62, 78, 54, 0.68);
}
.portrait-stage.mood-low .full-bg {
  filter: saturate(0.50) brightness(0.67) contrast(0.92) sepia(0.10) drop-shadow(0 14px 24px rgba(4, 4, 10, 0.28));
  transform: translateY(8px) rotate(-1.05deg);
}
.portrait-stage.mood-low::before { opacity: 0.42; filter: blur(30px); }
.portrait-stage.mood-plain .full-bg {
  filter: saturate(0.66) brightness(0.76) contrast(0.94) sepia(0.07) drop-shadow(0 16px 26px rgba(4, 4, 10, 0.32));
  transform: translateY(5px) rotate(-0.85deg);
}
.portrait-stage.mood-lazy .full-bg { animation: portraitBreathe 7.2s ease-in-out infinite; }
.portrait-stage.mood-happy .full-bg {
  filter: saturate(0.98) brightness(0.96) contrast(0.98) sepia(0.02) drop-shadow(0 18px 34px rgba(96, 122, 68, 0.30));
  animation: portraitBreathe 5.6s ease-in-out infinite;
}
.portrait-stage.mood-happy::before { opacity: 1; }
.portrait-stage.mood-excited .full-bg {
  filter: saturate(1.12) brightness(1.04) contrast(1.01) drop-shadow(0 20px 38px rgba(143, 181, 92, 0.34));
  animation: portraitBuoyant 3.8s ease-in-out infinite;
}
.portrait-stage.mood-excited .portrait-state-mark {
  background: rgba(79, 105, 53, 0.48);
  color: rgba(250, 244, 215, 0.78);
}
.portrait-stage.bond-initial { opacity: 0.50; transform: translateX(18px) scale(0.965); }
.portrait-stage.bond-familiar { opacity: 0.60; transform: translateX(10px) scale(0.985); }
.portrait-stage.bond-intimate { opacity: 0.70; transform: translateX(2px) scale(1); }
.portrait-stage.bond-lover { opacity: 0.76; transform: translateX(-4px) scale(1.025); }
@keyframes portraitBreathe {
  0%, 100% { transform: translateY(2px) rotate(-0.7deg); }
  50% { transform: translateY(-2px) rotate(-0.45deg); }
}
@keyframes portraitBuoyant {
  0%, 100% { transform: translateY(1px) rotate(-0.6deg); }
  50% { transform: translateY(-6px) rotate(-0.15deg); }
}
@media (prefers-reduced-motion: reduce) {
  .portrait-stage .full-bg { animation: none !important; }
}
.chibi-stage {
  position: absolute;
  z-index: 1;
  left: clamp(12px, 2.8vw, 46px);
  bottom: 74px;
  width: min(19vw, 218px);
  height: min(44vh, 334px);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  pointer-events: none;
  opacity: 0.54;
  transition: opacity 0.45s ease, transform 0.45s ease, filter 0.45s ease;
}
.chibi-glow {
  position: absolute;
  width: 120%;
  height: 68%;
  bottom: 1%;
  border-radius: 50%;
  background: radial-gradient(ellipse, rgba(224, 182, 137, 0.19) 0%, rgba(178, 145, 203, 0.09) 42%, transparent 71%);
  filter: blur(18px);
}
.chibi-bg {
  position: relative;
  z-index: 1;
  display: block;
  width: auto;
  max-width: 122%;
  height: 100%;
  object-fit: contain;
  object-position: bottom center;
  filter: saturate(0.82) brightness(0.88) contrast(0.94) drop-shadow(0 13px 21px rgba(4, 4, 10, 0.26));
  -webkit-mask-image: linear-gradient(to top, transparent 0%, rgba(0,0,0,0.68) 7%, #000 18%, #000 100%);
  mask-image: linear-gradient(to top, transparent 0%, rgba(0,0,0,0.68) 7%, #000 18%, #000 100%);
}
.chibi-stage.faded { opacity: 0.16; transform: translateX(-14px) scale(0.94); filter: blur(0.2px); }
.theme-light .chibi-stage { opacity: 0.48; }
.theme-light .chibi-glow { background: radial-gradient(ellipse, rgba(244, 204, 127, 0.22) 0%, rgba(162, 188, 116, 0.11) 43%, transparent 72%); }
.theme-light .chibi-bg { filter: saturate(0.82) brightness(1.04) contrast(0.93) sepia(0.05) drop-shadow(0 12px 20px rgba(91, 79, 44, 0.16)); }
.portrait-stage.faded { opacity: 0.24; transform: translateX(16px) scale(0.97); filter: blur(0.3px); }
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
  background-position: center center;
  background-repeat: no-repeat;
  filter: saturate(0.96) contrast(1.04);
  transform: scale(1.015);
  pointer-events: none;
}
.chat-view::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  background:
    radial-gradient(ellipse at 50% 32%, transparent 24%, rgba(17, 15, 27, 0.15) 100%),
    linear-gradient(90deg, rgba(23, 20, 35, 0.16), transparent 48%, rgba(23, 20, 35, 0.22));
}
.theme-light .chat-view::after {
  background:
    radial-gradient(ellipse at 50% 32%, transparent 28%, rgba(130, 121, 84, 0.05) 100%),
    linear-gradient(90deg, rgba(117, 136, 84, 0.06), transparent 50%, rgba(190, 152, 86, 0.07));
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
  position: relative;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: 28px;
  padding: 34px 54px 30px;
  box-shadow: 0 22px 70px rgba(10, 8, 20, 0.38), inset 0 1px 0 rgba(255, 255, 255, 0.09);
  background:
    linear-gradient(145deg, rgba(54, 50, 79, 0.88), rgba(33, 32, 49, 0.88)) padding-box,
    linear-gradient(135deg, rgba(255, 235, 246, 0.48), rgba(224, 194, 239, 0.18) 48%, rgba(255, 198, 220, 0.34)) border-box;
  max-width: 490px;
  text-align: center;
  animation: popIn 0.3s ease both;
}
.empty-card::before {
  content: '';
  width: 220px;
  height: 220px;
  position: absolute;
  top: -148px;
  right: -80px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255, 215, 228, 0.22), transparent 68%);
  pointer-events: none;
}
.empty-card::after {
  content: '✦';
  position: absolute;
  right: 26px;
  bottom: 18px;
  color: var(--accent-light);
  opacity: 0.38;
  font-size: 1.1rem;
}
.theme-light .empty-card {
  border-color: transparent;
  background:
    linear-gradient(145deg, rgba(255, 253, 247, 0.91), rgba(247, 242, 227, 0.84)) padding-box,
    linear-gradient(135deg, rgba(117, 142, 87, 0.38), rgba(247, 221, 165, 0.68) 50%, rgba(188, 127, 142, 0.34)) border-box;
  box-shadow: 0 22px 60px rgba(102, 88, 50, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.86);
  backdrop-filter: blur(16px) saturate(1.03);
}
.theme-light .empty-card::before {
  background: radial-gradient(circle, rgba(248, 209, 135, 0.26), rgba(177, 198, 134, 0.10) 40%, transparent 70%);
}
.theme-light .empty-card::after { color: #b27b76; opacity: 0.48; }
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
  width: 172px;
  height: 172px;
  transform: translate(-50%, -58%);
  border-radius: 50%;
  background: radial-gradient(circle, rgba(232, 143, 169, 0.24) 0%, var(--primary-soft) 42%, transparent 70%);
  filter: blur(6px);
  pointer-events: none;
}
.empty-avatar :deep(.portrait-wrap) {
  border-radius: 50%;
  border: 2px solid rgba(255, 225, 239, 0.48);
  box-shadow: 0 0 0 1px rgba(255, 231, 243, 0.25), 0 0 0 6px rgba(232, 143, 169, 0.1), 0 12px 30px rgba(11, 8, 20, 0.36);
  position: relative;
  overflow: hidden;
}
.theme-light .empty-avatar :deep(.portrait-wrap) {
  border-color: rgba(103, 134, 76, 0.44);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.86), 0 0 0 6px rgba(221, 185, 117, 0.15), 0 12px 28px rgba(93, 82, 46, 0.20);
}
.empty-orbit {
  position: absolute;
  border: 1px solid rgba(255, 215, 232, 0.25);
  border-radius: 50%;
  pointer-events: none;
}
.theme-light .empty-orbit { border-color: rgba(120, 145, 88, 0.34); }
.orbit-a { width: 142px; height: 142px; transform: translateY(-4px) rotate(-18deg) scaleX(1.18); }
.orbit-b { width: 158px; height: 158px; transform: translateY(-4px) rotate(34deg) scaleX(0.78); opacity: 0.6; }
.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--accent-light);
  font-size: 0.7rem;
  letter-spacing: 0.14em;
  margin-top: 2px;
}
.theme-light .eyebrow { color: #a56f71; }
.eyebrow span { width: 18px; height: 1px; background: currentColor; opacity: 0.7; }
.empty-avatar :deep(.portrait) {
  object-fit: cover;
  object-position: center 18%;
  border-radius: 50%;
}
.empty-vine {
  margin-top: 10px;
  filter: drop-shadow(0 2px 6px rgba(139, 165, 102, 0.2));
}
.empty .t { font-size: 1.18rem; color: var(--text); font-weight: 700; line-height: 1.5; letter-spacing: 0.7px; }
.empty .t-sub { font-size: 0.88rem; color: var(--text-dim); line-height: 1.75; max-width: 310px; }
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
  border: 1px solid var(--edge-subtle);
  background: rgba(255, 255, 255, 0.045);
  color: var(--text-dim);
  cursor: pointer;
  transition: all 0.18s ease;
  backdrop-filter: blur(8px);
}
.theme-light .hint-chip {
  background: rgba(255, 253, 247, 0.66);
  border-color: rgba(105, 132, 76, 0.20);
}
.hint-chip:hover {
  border-color: var(--edge-active);
  color: var(--accent-light);
  background: rgba(232, 143, 169, 0.13);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}
.theme-light .hint-chip:hover { background: rgba(244, 223, 181, 0.60); color: var(--primary-deep); }

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
  .chat-view::before { background-position: 50% center; }
  .portrait-stage { right: -38px; width: 230px; height: min(64%, 500px); opacity: 0.30; }
  .chibi-stage { left: -26px; width: 150px; height: 230px; opacity: 0.22; }
}
@media (max-width: 540px) {
  .portrait-stage { display: none; }
  .chibi-stage { display: none; }
}
</style>
