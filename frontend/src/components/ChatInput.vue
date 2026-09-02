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
</script>

<template>
  <div class="inputbar">
    <div class="input-box">
      <input ref="fileInput" type="file" accept="image/*" style="display:none" @change="emit('file', ($event.target as HTMLInputElement).files?.[0] || null)" />
      <button class="icon-btn" title="识图：上传图片让菟菚看看" @click="fileInput?.click()">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="4"/>
          <circle cx="8.5" cy="8.5" r="1.5"/>
          <path d="M21 15l-5-5L5 21"/>
        </svg>
      </button>
      <textarea v-model="input" rows="1" placeholder="和菟菚说点什么…（Enter 发送，Shift+Enter 换行）" @keydown.enter.exact.prevent="emit('send')"></textarea>
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
      <span class="tip">Enter 发送 · Shift+Enter 换行</span>
      <span class="foot-status" :class="{ live: streaming }">
        <span class="dot"></span>{{ streaming ? '正在输入…' : '菟丝缠绕' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.inputbar {
  padding: 10px 22px 12px;
  background: linear-gradient(to top, var(--bg) 70%, transparent);
  flex-shrink: 0;
}
.input-box {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 8px 10px;
  box-shadow: var(--shadow-md);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.input-box:focus-within {
  border-color: var(--primary);
  box-shadow: var(--glow), var(--shadow-md);
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
  color: var(--primary-deep);
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
  background: var(--bg-user);
  color: #fff;
  padding: 0 20px;
  height: 38px;
  font-size: 0.92rem;
  box-shadow: 0 3px 12px rgba(164, 184, 92, 0.4);
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
.stop:hover { background: #f7e2d8; }
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
.foot-status.live { color: var(--primary-deep); }

@media (max-width: 720px) {
  .inputbar { padding: 8px 10px 10px; }
  .send span { display: none; }
  .send { padding: 0 14px; }
}
</style>