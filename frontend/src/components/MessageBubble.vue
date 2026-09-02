<script setup lang="ts">
import { ref } from 'vue'
import type { Message } from '../api/sessions'
import { resolveImageSrc } from '../utils/images'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps<{ message: Message; isStreamingLast: boolean }>()

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

function openLightbox(src: string) {
  const lb = document.createElement('div')
  lb.style.cssText = 'position:fixed;inset:0;background:rgba(40,50,25,.82);backdrop-filter:blur(4px);z-index:100;display:flex;align-items:center;justify-content:center;cursor:zoom-out;animation:fadeIn .2s ease;'
  const im = document.createElement('img')
  im.src = src
  im.style.cssText = 'max-width:92vw;max-height:88vh;border-radius:14px;box-shadow:0 12px 60px rgba(0,0,0,.5);'
  lb.appendChild(im)
  lb.addEventListener('click', () => lb.remove())
  document.body.appendChild(lb)
}

const showStreamCursor = () =>
  props.message.role === 'bot' && !props.message.content && props.isStreamingLast
const canCopy = () =>
  props.message.role === 'bot' && !!props.message.content && !props.isStreamingLast
</script>

<template>
  <div class="msg" :class="message.role">
    <div class="avatar">{{ message.role === 'user' ? '你' : '菟' }}</div>
    <div class="col">
      <div
        class="bubble"
        :class="message.role"
        :style="message.role === 'bot' ? { borderRadius: '20px 20px 20px 8px' } : { borderRadius: '20px 20px 8px 20px' }"
      >
        <div v-if="message.role === 'bot' && message.content" class="md" v-html="renderMarkdown(message.content)"></div>
        <div v-else-if="showStreamCursor()" class="cursor-wrap">
          <span class="tendril-cursor">
            <svg width="32" height="16" viewBox="0 0 60 30">
              <path d="M5 15 Q 15 5, 25 15 T 45 15 T 55 10" fill="none" stroke="#a4b85c" stroke-width="2.5" stroke-linecap="round" class="tendril-draw"/>
            </svg>
          </span>
        </div>
        <template v-else>{{ message.content }}</template>
      </div>
      <img
        v-if="message.image"
        class="mdimg"
        :src="resolveImageSrc(message.image)"
        alt="生成的图片"
        @click="openLightbox(resolveImageSrc(message.image))"
      />
      <div class="meta" :class="{ pinned: copied }">
        <span class="time">{{ formatTime(message.ts) }}</span>
        <button
          v-if="canCopy()"
          class="copybtn"
          :class="{ ok: copied }"
          @click="copyText(message.content)"
        >
          {{ copied ? '✓' : '⧉' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  gap: 10px;
  max-width: 780px;
  animation: fadeUp 0.28s ease both;
}
.msg.user { align-self: flex-end; flex-direction: row-reverse; }
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
  background: linear-gradient(135deg, #c2d072, #a4b85c);
  color: #fff;
  box-shadow: 0 2px 10px rgba(164, 184, 92, 0.45);
}
.msg.bot .avatar {
  background: #fff;
  color: var(--primary-deep);
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
}
.bubble.user {
  background: var(--bg-user);
  color: #fff;
  border-radius: 20px 20px 8px 20px;
  box-shadow: 0 4px 14px rgba(164, 184, 92, 0.35);
}
.cursor-wrap {
  display: flex;
  align-items: center;
  min-height: 26px;
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
  background: none;
  border: none;
  color: var(--text-faint);
  cursor: pointer;
  font-size: 0.78rem;
  padding: 1px 4px;
  border-radius: 4px;
  transition: all 0.15s ease;
}
.copybtn:hover { color: var(--primary-deep); background: var(--primary-soft); }
.copybtn.ok { color: var(--primary-deep); }
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
</style>
