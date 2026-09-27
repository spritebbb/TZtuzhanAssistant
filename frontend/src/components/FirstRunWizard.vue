<script setup lang="ts">
// NP-06 首次运行向导：欢迎 → 填 Key → 桌面宠物（仅桌面壳）→ 完成。
// 原则：不自动发送任何消息；跳过也落 flag（老用户升级不弹）；失败可见不静默。
import { computed, ref, watch } from 'vue'
import { apiFetch } from '../api'
import { FIRST_RUN_FLAG } from '../utils/firstRun'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{ close: [] }>()

// 桌面壳判定：与 SettingsPanel 的 petAvailable 同一依据（preload 注入即桌面版）
const isDesktopShell = typeof window !== 'undefined' && !!window.tuzhanPet

type StepKind = 'welcome' | 'key' | 'pet' | 'done'
const stepKinds: StepKind[] = isDesktopShell
  ? ['welcome', 'key', 'pet', 'done']
  : ['welcome', 'key', 'done']
const stepIndex = ref(0)
const step = computed<StepKind>(() => stepKinds[stepIndex.value] ?? 'welcome')

function next() {
  if (stepIndex.value < stepKinds.length - 1) stepIndex.value += 1
}
function back() {
  if (stepIndex.value > 0) stepIndex.value -= 1
}

// ---- 步骤②：API Key（默认值取自当前配置；POST 仅送白名单三字段，其余不动）----
const baseUrl = ref('https://api.deepseek.com/v1')
const model = ref('deepseek-chat')
const apiKey = ref('')
const keyError = ref('')
const savingKey = ref(false)

async function loadConfigDefaults() {
  try {
    const r = await apiFetch('/api/config')
    const d = await r.json()
    if (d.config?.llm_base_url) baseUrl.value = String(d.config.llm_base_url)
    if (d.config?.llm_model) model.value = String(d.config.llm_model)
  } catch { /* 拉不到就用内置默认 */ }
}

async function saveKey() {
  if (savingKey.value) return
  if (!apiKey.value.trim()) {
    keyError.value = '请先填入 API Key；也可以点右上角「跳过」，之后在设置里填。'
    return
  }
  savingKey.value = true
  keyError.value = ''
  try {
    const r = await apiFetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        llm_base_url: baseUrl.value.trim(),
        llm_model: model.value.trim(),
        llm_api_key: apiKey.value.trim(),
      }),
    })
    const d = await r.json()
    if (!r.ok || !d.ok) throw new Error(d.error || '保存失败')
    next()
  } catch (e: unknown) {
    keyError.value = '保存失败：' + ((e as Error).message || e)
  } finally {
    savingKey.value = false
  }
}

// ---- 步骤③：桌面宠物（初始值来自真实状态查询，不再恒 false）----
const petOn = ref(false)

async function initPetState() {
  if (!window.tuzhanPet) return
  try {
    petOn.value = await window.tuzhanPet.getPetState()
  } catch { /* 查询失败保持默认 */ }
}

async function togglePet() {
  if (!window.tuzhanPet) return
  try {
    petOn.value = await window.tuzhanPet.togglePet()
  } catch { /* 切换失败保持显示 */ }
}

// ---- 完成 / 跳过：都落 flag；完成路径预填「新手教程」并聚焦（不自动发送）----
function finish(prefill: boolean) {
  try {
    localStorage.setItem(FIRST_RUN_FLAG, String(Date.now()))
  } catch { /* 隐私模式等场景下忽略 */ }
  if (prefill) {
    window.dispatchEvent(new CustomEvent('tztuzhan:prefill-input', { detail: '新手教程' }))
  }
  emit('close')
}

watch(
  () => props.show,
  (v) => {
    if (!v) return
    stepIndex.value = 0
    apiKey.value = ''
    keyError.value = ''
    void loadConfigDefaults()
    void initPetState()
  },
)
</script>

<template>
  <div v-if="show" class="frw-overlay" role="dialog" aria-modal="true" aria-label="首次使用向导">
    <div class="frw-card">
      <div class="frw-head">
        <span class="frw-progress">第 {{ stepIndex + 1 }} / {{ stepKinds.length }} 步</span>
        <button class="frw-skip" @click="finish(false)">跳过</button>
      </div>

      <div v-if="step === 'welcome'" class="frw-body">
        <div class="frw-title">第一次见面，我是{{ props.personaName || '菟菚' }}</div>
        <p>一个记得住你、有性格有情绪的本地 AI 伙伴。</p>
        <p>我慢热——关系是攒出来的，你说过的话我都记着。</p>
        <p>所有记录只存在这台电脑上，不会上传。</p>
        <div class="frw-actions">
          <button class="frw-primary" @click="next">开始设置（约 1 分钟）</button>
        </div>
      </div>

      <div v-else-if="step === 'key'" class="frw-body">
        <div class="frw-title">先给我一颗「大脑」</div>
        <p>填一个 OpenAI 兼容服务的 API Key（如 DeepSeek）；日常聊天大约每天几毛钱，随时可在设置里改。</p>
        <label class="frw-field"><span>API 地址</span><input v-model="baseUrl" type="text" aria-label="API 地址" /></label>
        <label class="frw-field"><span>模型名</span><input v-model="model" type="text" aria-label="模型名" /></label>
        <label class="frw-field"><span>API Key</span><input v-model="apiKey" type="password" aria-label="API Key" placeholder="sk-…" /></label>
        <div v-if="keyError" class="frw-error" role="alert">{{ keyError }}</div>
        <div class="frw-actions">
          <button class="frw-ghost" @click="back">上一步</button>
          <button class="frw-primary" :disabled="savingKey" @click="saveKey">{{ savingKey ? '保存中…' : '保存并继续' }}</button>
        </div>
      </div>

      <div v-else-if="step === 'pet'" class="frw-body">
        <div class="frw-title">要我在桌角陪你吗？</div>
        <p>桌面宠物是一扇常驻桌角的小窗：可拖拽、不打扰、你全屏时自动避让。</p>
        <label class="frw-field frw-check">
          <input type="checkbox" :checked="petOn" aria-label="开启桌面宠物" @change="togglePet" />
          <span>开启桌面宠物</span>
        </label>
        <div class="frw-actions">
          <button class="frw-ghost" @click="back">上一步</button>
          <button class="frw-primary" @click="next">下一步</button>
        </div>
      </div>

      <div v-else class="frw-body">
        <div class="frw-title">都准备好了</div>
        <p>点下面的按钮，我会现场演示一遍我会什么；之后随时可以在「设置」里调整一切。</p>
        <div class="frw-actions">
          <button class="frw-primary" @click="finish(true)">带我看你会什么</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.frw-overlay {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(10, 12, 8, 0.6);
  backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.frw-card {
  width: min(430px, 94vw);
  background: var(--bg-card);
  border: 1px solid var(--edge-highlight);
  border-radius: var(--radius-lg, 16px);
  box-shadow: var(--shadow-lg, 0 24px 64px rgba(0, 0, 0, 0.4));
  padding: 18px 22px 22px;
}
.frw-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.frw-progress {
  font-size: 0.72rem;
  color: var(--text-faint);
}
.frw-skip {
  border: none;
  background: none;
  color: var(--text-faint);
  font: inherit;
  font-size: 0.76rem;
  cursor: pointer;
  padding: 3px 8px;
  border-radius: var(--radius-sm, 8px);
}
.frw-skip:hover { color: var(--text); background: var(--bg-hover, rgba(255, 255, 255, 0.06)); }
.frw-body p {
  font-size: 0.86rem;
  color: var(--text-dim);
  line-height: 1.8;
  margin: 6px 0;
}
.frw-title {
  font-size: 1.06rem;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 8px;
}
.frw-field {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0;
  font-size: 0.82rem;
  color: var(--text-dim);
}
.frw-field span { flex-shrink: 0; width: 62px; }
.frw-field input[type='text'],
.frw-field input[type='password'] {
  flex: 1;
  min-width: 0;
  background: var(--bg-input);
  border: 1px solid var(--border, rgba(255, 255, 255, 0.1));
  border-radius: var(--radius-sm, 8px);
  color: var(--text);
  font: inherit;
  padding: 7px 10px;
  outline: none;
}
.frw-field input:focus { border-color: var(--edge-active); }
.frw-check { gap: 10px; }
.frw-check span { width: auto; }
.frw-error {
  font-size: 0.78rem;
  color: var(--danger, #d98a75);
  margin: 8px 0;
}
.frw-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
.frw-primary {
  border: none;
  border-radius: var(--radius-md, 10px);
  background: linear-gradient(135deg, #be7f9e, #82649f);
  color: #fff;
  font: inherit;
  font-weight: 600;
  padding: 8px 18px;
  cursor: pointer;
}
.frw-primary:disabled { opacity: 0.55; cursor: not-allowed; }
.frw-ghost {
  border: 1px solid var(--border, rgba(255, 255, 255, 0.14));
  border-radius: var(--radius-md, 10px);
  background: none;
  color: var(--text-dim);
  font: inherit;
  padding: 8px 14px;
  cursor: pointer;
}
.frw-ghost:hover { color: var(--text); }
</style>
