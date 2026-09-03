<script setup lang="ts">
import { ref, computed, onBeforeUnmount } from 'vue'
import type { Message } from '../api/sessions'
import { resolveImageSrc } from '../utils/images'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps<{
  message: Message
  isStreamingLast: boolean
  searchQuery?: string
  isMatch?: boolean
  isActiveMatch?: boolean
  toolStatus?: string
}>()

// ---- 自包含状态：复制反馈 / 图片灯箱 ----
const copied = ref(false)

function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (sameDay) return hm
  return `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch { /* ignore */ }
}

// ---- 增强灯箱：预览 + 下载 ----
const lightboxSrc = ref('')
const lightboxAlt = ref('')

function openLightbox(src: string, alt = '') {
  lightboxSrc.value = src
  lightboxAlt.value = alt
}
function closeLightbox() {
  lightboxSrc.value = ''
}

// Esc 关闭灯箱
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && lightboxSrc.value) closeLightbox()
}
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
if (typeof document !== 'undefined') document.addEventListener('keydown', onKeydown)

// 气泡 markdown 渲染：始终渲染 message.content 为 HTML（带 XSS 防护）。
// 搜索高亮由父组件传入的 isMatch/isActiveMatch 驱动边框样式（不在 HTML 内部注入 <mark>，
// 避免破坏已渲染的 HTML 结构；之前的实现把"无搜索词 → 返回空串"导致气泡内容被吞，是 GUI 空白气泡的根因）。
const renderedContent = computed(() => {
  return props.message.content ? renderMarkdown(props.message.content) : ''
})

const showStreamCursor = () =>
  props.message.role === 'bot' && !props.message.content && props.isStreamingLast
const canCopy = () =>
  props.message.role === 'bot' && !!props.message.content && !props.isStreamingLast

const imgSrc = computed(() => props.message.image ? resolveImageSrc(props.message.image) : '')
</script>

<template>
  <div
    class="msg"
    :class="[
      message.role,
      { 'match-hit': isMatch && !isActiveMatch },
      { 'match-active': isActiveMatch },
    ]"
  >
    <div class="avatar">{{ message.role === 'user' ? '你' : '菟' }}</div>
    <div class="col">
      <div
        class="bubble"
        :class="message.role"
        :style="message.role === 'bot' ? { borderRadius: '20px 20px 20px 8px' } : { borderRadius: '20px 20px 8px 20px' }"
      >
        <div v-if="message.content" class="md" v-html="renderedContent"></div>
        <div v-else-if="showStreamCursor()" class="cursor-wrap">
          <span class="tendril-cursor">
            <svg width="32" height="16" viewBox="0 0 60 30">
              <path d="M5 15 Q 15 5, 25 15 T 45 15 T 55 10" fill="none" stroke="var(--primary)" stroke-width="2.5" stroke-linecap="round" class="tendril-draw"/>
            </svg>
          </span>
          <span v-if="toolStatus" class="tool-status">{{ toolStatus }}</span>
        </div>
        <template v-else>{{ message.content }}</template>
      </div>
      <img
        v-if="message.image"
        class="mdimg"
        :src="imgSrc"
        :alt="message.content ? message.content.slice(0, 40) : '图片'"
        @click="openLightbox(imgSrc, message.content)"
      />
      <div class="meta" :class="{ pinned: copied }">
        <span class="time">{{ formatTime(message.ts) }}</span>
        <button
          v-if="canCopy()"
          class="copybtn"
          :class="{ ok: copied }"
          @click="copyText(message.content)"
          title="复制"
        >
          <svg v-if="!copied" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>
        </button>
      </div>
    </div>

    <!-- 增强灯箱 -->
    <Teleport to="body">
      <div v-if="lightboxSrc" class="lb-mask" @click="closeLightbox">
        <div class="lb-box" @click.stop>
          <img :src="lightboxSrc" :alt="lightboxAlt" class="lb-img" />
          <div class="lb-bar">
            <a class="lb-dl" :href="lightboxSrc" target="_blank" rel="noopener" download>下载原图</a>
            <button class="lb-x" @click="closeLightbox">✕</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  gap: 10px;
  max-width: 780px;
  animation: fadeUp 0.28s ease both;
  border-radius: var(--radius-lg);
  transition: background 0.2s ease;
}
.msg.user { align-self: flex-end; flex-direction: row-reverse; }
.msg.match-hit { background: var(--primary-soft); }
.msg.match-active {
  background: rgba(217, 168, 96, 0.16);
  box-shadow: inset 0 0 0 2px var(--border-strong);
}
.avatar {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.92rem;
  font-weight: 600;
}
.msg.user .avatar {
  background: var(--bg-user);
  color: #fff;
  box-shadow: 0 2px 10px rgba(124, 154, 85, 0.4);
}
.msg.bot .avatar {
  background: var(--bg-card);
  color: var(--primary-text);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}
.col { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.bubble {
  padding: 11px 15px;
  line-height: 1.65;
  font-size: 0.94rem;
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
  transition: box-shadow 0.18s ease;
  border-radius: 20px 20px 20px 8px; /* 有机感：局部圆角 */
}
.bubble.bot {
  background: var(--bg-bubble);
  border: 1px solid var(--border);
  border-radius: 20px 20px 20px 8px;
  box-shadow: var(--shadow-sm);
  backdrop-filter: blur(10px);
}
.bubble.user {
  background: var(--bg-user);
  color: #fff;
  border-radius: 20px 20px 8px 20px;
  box-shadow: 0 4px 14px rgba(124, 154, 85, 0.3);
}
.cursor-wrap {
  display: flex;
  align-items: center;
  min-height: 26px;
  gap: 8px;
}
.tool-status {
  font-size: 0.8rem;
  color: var(--text-dim);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  animation: fadeUp 0.2s ease both;
}
.tool-status::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--primary);
  animation: pulse 1.1s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.3; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.1); }
}
.tendril-cursor {
  display: inline-flex;
  align-items: center;
}
.tendril-draw {
  stroke-dasharray: 80;
  stroke-dashoffset: 0;
  animation: tendrilDraw 1.5s ease-in-out infinite;
}
@keyframes tendrilDraw {
  0% { stroke-dashoffset: 80; }
  50% { stroke-dashoffset: 0; }
  100% { stroke-dashoffset: 0; }
}
.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 4px;
  opacity: 0;
  transition: opacity 0.18s ease;
  min-height: 16px;
}
.msg:hover .meta, .msg .meta.pinned { opacity: 1; }
.time { font-size: 0.7rem; color: var(--text-faint); }
.copybtn {
  display: flex;
  align-items: center;
  background: none;
  border: none;
  color: var(--text-faint);
  cursor: pointer;
  padding: 1px 4px;
  border-radius: 4px;
  transition: all 0.15s ease;
}
.copybtn:hover { color: var(--primary-text); background: var(--primary-soft); }
.copybtn.ok { color: var(--primary-text); }
.mdimg {
  max-width: 340px;
  border-radius: var(--radius-md);
  margin-top: 4px;
  display: block;
  cursor: zoom-in;
  box-shadow: var(--shadow-md);
  transition: transform 0.2s ease;
}
.mdimg:hover { transform: scale(1.01); }

/* 灯箱 */
.lb-mask {
  position: fixed;
  inset: 0;
  background: rgba(10, 15, 10, 0.82);
  backdrop-filter: blur(6px);
  z-index: 300;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: fadeIn 0.2s ease;
}
.lb-box {
  max-width: 92vw;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  gap: 10px;
  animation: popIn 0.25s ease both;
}
.lb-img {
  max-width: 92vw;
  max-height: 84vh;
  border-radius: var(--radius-md);
  box-shadow: 0 16px 70px rgba(0, 0, 0, 0.55);
  object-fit: contain;
}
.lb-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.lb-dl {
  font-size: 0.8rem;
  color: var(--primary-text);
  background: var(--primary-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 5px 14px;
  text-decoration: none;
  transition: all 0.15s;
}
.lb-dl:hover { background: var(--primary); color: var(--text-invert); }
.lb-x {
  width: 32px; height: 32px;
  border: none;
  background: var(--danger-soft);
  color: var(--danger);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 0.9rem;
}
.lb-x:hover { background: var(--danger); color: #fff; }
</style>