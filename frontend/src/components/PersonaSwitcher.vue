<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  activatePersona,
  importPersona,
  listPersonas,
  updatePersona,
  type PersonaProfile,
} from '../api/personas'

const props = defineProps<{ show: boolean; disabled?: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'switched', profile: PersonaProfile): void
}>()

const personas = ref<PersonaProfile[]>([])
const active = ref<PersonaProfile | null>(null)
const busy = ref('')
const error = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const name = ref('')
const subtitle = ref('')
const theme = ref<'dark' | 'light'>('dark')
const voice = ref('zh-CN-XiaoxiaoNeural')

function fillForm(profile: PersonaProfile) {
  active.value = profile
  name.value = profile.name
  subtitle.value = profile.subtitle || ''
  theme.value = profile.theme || 'dark'
  voice.value = profile.voice || 'zh-CN-XiaoxiaoNeural'
}

async function load() {
  error.value = ''
  try {
    const data = await listPersonas()
    personas.value = data.personas
    fillForm(data.active)
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function switchTo(profile: PersonaProfile) {
  if (props.disabled || profile.active || busy.value) return
  busy.value = profile.id
  error.value = ''
  try {
    const next = await activatePersona(profile.id)
    await load()
    emit('switched', next)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

async function onFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || busy.value) return
  busy.value = 'import'
  error.value = ''
  try {
    const next = await importPersona(file)
    await load()
    emit('switched', next)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

async function saveSettings() {
  if (!active.value || busy.value) return
  busy.value = 'save'
  error.value = ''
  try {
    const next = await updatePersona(active.value.id, {
      name: name.value,
      subtitle: subtitle.value,
      theme: theme.value,
      voice: voice.value,
    })
    await load()
    emit('switched', next)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

watch(() => props.show, value => { if (value) void load() })
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="persona-mask" @click.self="emit('close')">
      <section class="persona-panel" role="dialog" aria-modal="true" aria-label="人格替换助手">
        <header>
          <div>
            <span class="eyebrow">PERSONA LIBRARY</span>
            <h2>人格替换助手</h2>
            <p>切换会同时恢复该人格的对话、记忆、知识库与界面设置。</p>
          </div>
          <button class="close" aria-label="关闭" @click="emit('close')">×</button>
        </header>

        <div class="body">
          <div class="library-head">
            <b>人格档案</b>
            <button class="import" :disabled="!!busy || disabled" @click="fileInput?.click()">
              {{ busy === 'import' ? '加载中…' : '加载 .md 人格卡' }}
            </button>
            <input ref="fileInput" class="hidden" type="file" accept=".md,text/markdown,text/plain" @change="onFile" />
          </div>
          <div class="persona-list">
            <button
              v-for="profile in personas"
              :key="profile.id"
              class="persona-card"
              :class="{ active: profile.active }"
              :disabled="!!busy || disabled"
              @click="switchTo(profile)"
            >
              <span class="avatar">{{ profile.name.slice(0, 1) }}</span>
              <span class="card-copy">
                <strong>{{ profile.name }}</strong>
                <small>{{ profile.subtitle || '独立人格档案' }}</small>
              </span>
              <span class="status">{{ profile.active ? '使用中' : busy === profile.id ? '切换中…' : '切换' }}</span>
            </button>
          </div>

          <div v-if="active" class="settings">
            <div class="settings-title">
              <b>当前人格设置</b>
              <span>随人格保存，切回时自动恢复</span>
            </div>
            <label><span>名称</span><input v-model="name" maxlength="40" /></label>
            <label><span>副标题</span><input v-model="subtitle" maxlength="80" /></label>
            <label>
              <span>主题背景</span>
              <select v-model="theme"><option value="dark">月夜暗色</option><option value="light">日间亮色</option></select>
            </label>
            <label><span>Edge TTS 音色</span><input v-model="voice" maxlength="120" /></label>
            <button class="save" :disabled="!!busy" @click="saveSettings">{{ busy === 'save' ? '保存中…' : '保存当前人格设置' }}</button>
          </div>
          <p v-if="disabled" class="warning">正在生成回复，结束后才能安全切换人格。</p>
          <p v-if="error" class="error">{{ error }}</p>
          <p class="footnote">人格卡及设置保存在 data/personas；聊天、记忆和知识库使用独立命名空间，不会互相串用。</p>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.persona-mask { position: fixed; inset: 0; z-index: 1300; display: grid; place-items: center; padding: 20px; background: rgba(8, 9, 16, .66); backdrop-filter: blur(7px); }
.persona-panel { width: min(680px, 96vw); max-height: 88vh; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: linear-gradient(155deg, var(--bg-card), var(--bg-main, var(--bg))); border: 1px solid var(--border-light); border-radius: var(--radius-lg); box-shadow: var(--shadow-lg); }
header { display: flex; justify-content: space-between; gap: 18px; padding: 22px 24px 17px; border-bottom: 1px solid var(--border); }
.eyebrow { display: block; margin-bottom: 4px; color: var(--accent); font-size: .62rem; letter-spacing: .18em; }
h2 { margin: 0; font-size: 1.18rem; }
header p { margin: 5px 0 0; color: var(--text-faint); font-size: .78rem; }
.close { width: 32px; height: 32px; border: 0; border-radius: 9px; color: var(--text-faint); background: transparent; font-size: 24px; cursor: pointer; }
.close:hover { color: var(--danger); background: var(--danger-soft); }
.body { padding: 18px 24px 22px; overflow-y: auto; }
.library-head, .settings-title { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.library-head b, .settings-title b { font-size: .88rem; }
.import, .save { margin-left: auto; padding: 7px 13px; border: 0; border-radius: 9px; color: #fff; background: var(--bg-user); cursor: pointer; font-weight: 600; }
.hidden { display: none; }
.persona-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.persona-card { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 11px; text-align: left; color: var(--text); background: var(--bg-card); border: 1px solid var(--border); border-radius: 13px; cursor: pointer; }
.persona-card:hover { border-color: var(--primary); }
.persona-card.active { border-color: var(--accent); background: var(--primary-soft); }
.avatar { display: grid; place-items: center; width: 38px; height: 38px; flex: 0 0 auto; border-radius: 12px; color: var(--text-invert); background: linear-gradient(135deg, var(--accent), var(--primary)); font-weight: 800; }
.card-copy { min-width: 0; display: flex; flex: 1; flex-direction: column; gap: 2px; }
.card-copy strong, .card-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-copy strong { font-size: .86rem; }.card-copy small { color: var(--text-faint); font-size: .7rem; }
.status { color: var(--primary-text); font-size: .68rem; }
.settings { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); }
.settings-title span { color: var(--text-faint); font-size: .7rem; }
label { display: grid; grid-template-columns: 105px 1fr; align-items: center; gap: 10px; margin: 9px 0; color: var(--text-dim); font-size: .78rem; }
input, select { min-width: 0; padding: 8px 10px; color: var(--text); background: var(--bg-input); border: 1px solid var(--border); border-radius: 9px; outline: none; }
input:focus, select:focus { border-color: var(--primary); box-shadow: var(--glow); }
.save { display: block; margin: 12px 0 0 auto; }
button:disabled { opacity: .5; cursor: not-allowed; }
.warning, .error, .footnote { margin: 10px 0 0; font-size: .72rem; line-height: 1.5; }
.warning { color: var(--accent); }.error { color: var(--danger); }.footnote { color: var(--text-faint); }
@media (max-width: 580px) { .persona-list { grid-template-columns: 1fr; } label { grid-template-columns: 1fr; gap: 4px; } }
</style>
