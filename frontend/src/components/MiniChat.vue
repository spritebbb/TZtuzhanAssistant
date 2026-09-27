<script setup lang="ts">
// NP-11 迷你速聊窗：全局热键唤出，问完即走。
// 设计：直连 streamChat（会话用 current，消息由后端正常落库——主窗打开即见完整
// 历史）；本窗不维护会话状态；不渲染立绘/工具进度详情（空间不够）。
// 确认类工具不在窗内确认——显示一行引导去主窗口。
import { nextTick, ref } from 'vue'

import { streamChat } from '../api/chat'
import { CURRENT_SESSION_ID } from '../api/sessions'

const input = ref('')
const reply = ref('')
const busy = ref(false)
const toolLine = ref('')
const confirmHint = ref('')
const errorLine = ref('')
let controller: AbortController | null = null

// 答完 3 秒自动隐藏（拍板：说完即走；内容永远在主窗历史里，不丢）
let hideTimer: ReturnType<typeof setTimeout> | null = null
function scheduleHide() {
  if (hideTimer) clearTimeout(hideTimer)
  hideTimer = setTimeout(() => { window.tuzhanMini?.hide() }, 3000)
}
function cancelHide() {
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null }
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  cancelHide()
  busy.value = true
  reply.value = ''
  toolLine.value = ''
  confirmHint.value = ''
  errorLine.value = ''
  controller = new AbortController()
  try {
    await streamChat(text, CURRENT_SESSION_ID, controller.signal, {
      onPiece: (piece) => { reply.value += piece },
      onTool: (ev) => {
        if (ev.type === 'tool' && ev.name) toolLine.value = `正在${ev.name}…`
        else if (ev.type === 'tool_done') toolLine.value = ''
      },
      onConfirmRequest: () => {
        confirmHint.value = '这步需要你确认——请到主窗口处理'
      },
      onDone: () => { toolLine.value = '' },
      onError: (err) => { errorLine.value = err || '出了点问题，稍后再试' },
    })
  } catch (e: unknown) {
    if (!(controller?.signal.aborted)) errorLine.value = (e as Error).message || '发送失败'
  } finally {
    busy.value = false
    controller = null
    input.value = ''
    await nextTick()
    scheduleHide()
  }
}

function stop() {
  controller?.abort()
  controller = null
  busy.value = false
  toolLine.value = ''
  scheduleHide()
}

function onEnterKey(e: KeyboardEvent) {
  // 与主窗同款 IME 守卫（NP-01 语义在此窗同样适用）
  if (e.isComposing || e.keyCode === 229) return
  e.preventDefault()
  void send()
}
</script>

<template>
  <div class="mini" @pointerdown="cancelHide">
    <div class="mini-row">
      <span class="mini-dot" :class="{ live: busy }"></span>
      <input
        v-model="input"
        class="mini-input"
        type="text"
        aria-label="快问快答"
        placeholder="问点什么…（Enter 发送）"
        :disabled="false"
        @keydown.enter.exact="onEnterKey"
      />
      <button v-if="busy" class="mini-stop" aria-label="停止生成" title="停止" @click="stop">■</button>
    </div>
    <div v-if="reply || toolLine || confirmHint || errorLine" class="mini-reply">
      <div v-if="toolLine" class="mini-tool">{{ toolLine }}</div>
      <div v-if="confirmHint" class="mini-confirm">{{ confirmHint }}</div>
      <div v-if="errorLine" class="mini-error" role="alert">{{ errorLine }}</div>
      <div v-else-if="reply" class="mini-text">{{ reply }}</div>
    </div>
  </div>
</template>

<style scoped>
.mini {
  height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  box-sizing: border-box;
  background: rgba(22, 26, 20, 0.92);
  border: 1px solid rgba(139, 165, 102, 0.35);
  border-radius: 14px;
  backdrop-filter: blur(10px);
  font-family: inherit;
  color: #e8eadf;
}
.mini-row { display: flex; align-items: center; gap: 8px; }
.mini-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: #8ba566; flex-shrink: 0;
}
.mini-dot.live { animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
.mini-input {
  flex: 1; min-width: 0;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 10px;
  color: #e8eadf;
  font: inherit; font-size: 0.9rem;
  padding: 8px 12px;
  outline: none;
}
.mini-input:focus { border-color: #8ba566; }
.mini-input::placeholder { color: rgba(232, 234, 223, 0.4); }
.mini-stop {
  border: none; border-radius: 8px;
  background: rgba(224, 138, 109, 0.2); color: #e08a6d;
  width: 32px; height: 32px; cursor: pointer; flex-shrink: 0;
  font-size: 0.7rem;
}
.mini-reply {
  flex: 1; min-height: 0; overflow-y: auto;
  font-size: 0.84rem; line-height: 1.65;
}
.mini-tool { color: rgba(232, 234, 223, 0.55); font-size: 0.74rem; margin-bottom: 4px; }
.mini-confirm { color: #d9a441; font-size: 0.76rem; }
.mini-error { color: #e08a6d; font-size: 0.8rem; }
.mini-text { white-space: pre-wrap; word-break: break-word; }
@media (prefers-reduced-motion: reduce) { .mini-dot.live { animation: none; } }
</style>
