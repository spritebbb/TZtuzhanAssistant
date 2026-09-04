<script setup lang="ts">
import { ref } from 'vue'

const input = defineModel<string>('input', { required: true })
const emit = defineEmits<{
  send: []
  stop: []
  file: [f: File | null]
}>()

defineProps<{ busy: boolean; streaming: boolean }>()

const fileInput = ref<HTMLInputElement | null>(null)

// === 快捷指令面板 ===
const shortcutsOpen = ref(false)

interface Shortcut {
  icon: string
  label: string
  prompt: string
}
const shortcuts: Shortcut[] = [
  { icon: '⛅', label: '查天气', prompt: '帮我查一下今天襄阳的天气' },
  { icon: '🎨', label: '画张图', prompt: '帮我画一张好看的图' },
  { icon: '🔎', label: '联网搜索', prompt: '帮我搜索一下最新信息：' },
  { icon: '📝', label: '写作文案', prompt: '帮我写一份关于菟丝子的文案' },
  { icon: '📋', label: '整理要点', prompt: '帮我整理一下这段内容的要点：' },
  { icon: '🖥️', label: '查本机状态', prompt: '帮我看看本机的系统状态' },
  { icon: '🌿', label: '聊菟丝子', prompt: '聊聊菟丝子吧' },
  { icon: '🧠', label: '回忆一下', prompt: '你还记得我们之前聊过的什么吗？' },
]

function useShortcut(s: Shortcut) {
  input.value = s.prompt
  shortcutsOpen.value = false
}
</script>

<template>
  <div class="inputbar">
    <!-- 快捷指令面板 -->
    <div v-if="shortcutsOpen" class="shortcuts glass">
      <div class="sc-head">
        <span>快捷指令</span>
        <button class="sc-close" title="收起" @click="shortcutsOpen = false">✕</button>
      </div>
      <div class="sc-grid">
        <button v-for="s in shortcuts" :key="s.label" class="sc-item" @click="useShortcut(s)">
          <span class="sc-ic">{{ s.icon }}</span>
          <span class="sc-label">{{ s.label }}</span>
        </button>
      </div>
    </div>

    <div class="input-box">
      <input ref="fileInput" type="file" accept="image/*" style="display:none" @change="emit('file', ($event.target as HTMLInputElement).files?.[0] || null)" />
      <button class="icon-btn" title="快捷指令" @click="shortcutsOpen = !shortcutsOpen">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 6h16M4 12h16M4 18h16"/>
        </svg>
      </button>
      <button class="icon-btn" title="识图：上传图片让菟菚看看" @click="fileInput?.click()">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="4"/>
          <circle cx="8.5" cy="8.5" r="1.5"/>
          <path d="M21 15l-5-5L5 21"/>
        </svg>
      </button>
      <textarea v-model="input" :disabled="busy" rows="1" placeholder="和菟菚说点什么…（Enter 发送，Shift+Enter 换行）" @keydown.enter.exact.prevent="emit('send')"></textarea>
      <button v-if="streaming" class="btn stop" @click="emit('stop')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
      </button>
      <button class="btn send" :disabled="busy" @click="emit('send')">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 2L11 13"/>
          <path d="M22 2l-7 20-4-9-9-4 20-7z"/>
        </svg>
        <span>发送</span>
      </button>
    </div>
    <div class="inputbar-foot">
      <span class="tip">Enter 发送 · Shift+Enter 换行 · Ctrl+Shift+F 对话内搜索</span>
      <span class="foot-status" :class="{ live: streaming }">
        <span class="dot"></span>{{ streaming ? '正在输入…' : '菟丝缠绕' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.inputbar {
  padding: 12px 22px 14px;
  background: linear-gradient(to top, rgba(21, 21, 34, 0.98) 62%, transparent);
  flex-shrink: 0;
  position: relative;
}
.theme-light .inputbar {
  background: linear-gradient(to top, rgba(248, 243, 231, 0.96) 62%, rgba(248, 243, 231, 0.12));
}
.input-box {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  background: var(--bg-input);
  border: 1px solid var(--edge-highlight);
  border-radius: 20px;
  padding: 9px 11px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.05),
    0 14px 38px rgba(10, 8, 20, 0.26),
    inset 0 1px 0 var(--surface-shine);
  backdrop-filter: blur(18px) saturate(1.2);
  -webkit-backdrop-filter: blur(18px) saturate(1.2);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.theme-light .input-box {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    var(--shadow-md);
}
.input-box:focus-within {
  border-color: var(--edge-active);
  box-shadow: var(--glow), var(--shadow-md), inset 0 1px 0 var(--surface-shine);
}
.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  background: none;
  border: none;
  color: var(--text-dim);
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.18s ease;
}
.icon-btn:hover {
  background: var(--primary-soft);
  box-shadow: inset 0 0 0 1px var(--edge-subtle);
  color: var(--primary-text);
}
textarea {
  flex: 1;
  background: transparent;
  border: none;
  padding: 8px 6px;
  color: var(--text);
  font-size: 0.95rem;
  outline: none;
  resize: none;
  max-height: 140px;
  font-family: inherit;
  line-height: 1.55;
}
textarea::placeholder { color: var(--text-faint); }
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-weight: 600;
  transition: all 0.18s ease;
}
.send {
  background: linear-gradient(135deg, #be7f9e, #82649f);
  color: #fff;
  padding: 0 20px;
  height: 38px;
  font-size: 0.92rem;
  box-shadow: 0 5px 16px rgba(170, 100, 145, 0.36);
}
.send:hover { background: var(--bg-user-deep); transform: translateY(-1px); }
.send:active { transform: translateY(0); }
.send:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
.stop {
  background: var(--danger-soft);
  color: var(--danger);
  width: 38px;
  height: 38px;
}
.stop:hover { background: rgba(224, 138, 109, 0.25); }
.inputbar-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 6px 0;
}
.tip { font-size: 0.68rem; color: var(--text-faint); }
.foot-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 0.68rem;
  color: var(--text-faint);
}
.foot-status .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--primary);
  box-shadow: 0 0 5px var(--primary);
}
.foot-status.live .dot { animation: breathe 1.2s ease-in-out infinite; }
.foot-status.live { color: var(--primary-text); }

/* 快捷指令面板 */
.shortcuts {
  position: absolute;
  left: 22px;
  right: 22px;
  bottom: calc(100% - 6px);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  box-shadow: var(--shadow-lg);
  animation: fadeUp 0.22s ease both;
  z-index: 20;
}
.sc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--text-dim);
  margin-bottom: 10px;
}
.sc-close {
  border: none;
  background: none;
  color: var(--text-faint);
  cursor: pointer;
  font-size: 0.85rem;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}
.sc-close:hover { background: var(--danger-soft); color: var(--danger); }
.sc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}
.sc-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-dim);
  cursor: pointer;
  transition: all 0.16s ease;
  font-size: 0.82rem;
}
.sc-item:hover {
  border-color: var(--primary);
  color: var(--primary-text);
  background: var(--primary-soft);
  transform: translateY(-1px);
}
.sc-ic { font-size: 1.05rem; }
.sc-label { white-space: nowrap; }

@media (max-width: 768px) {
  .inputbar { padding: 8px 10px 10px; }
  .send span { display: none; }
  .send { padding: 0 14px; }
  .shortcuts { left: 10px; right: 10px; }
}
</style>
