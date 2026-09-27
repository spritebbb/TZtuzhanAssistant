<script setup lang="ts">
// 酒馆同玩（TAVERN-COMPANION）：她以真身进入你本机 SillyTavern 的一局剧情，
// 演完沉淀为她的真回忆。面板职责：玩法指引 + tavern_enabled 开关。
// 需要本机 SillyTavern + 「菟菚同伴」扩展配合；不开开关时后端 /api/tavern/* 一律拒绝。
import { ref, watch } from 'vue'
import { apiFetch } from '../api'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{ close: [] }>()

const enabled = ref(true)
const busy = ref(false)
const msg = ref('')

async function load() {
  try {
    const r = await apiFetch('/api/flags')
    const d = await r.json()
    if (d.ok && 'tavern_enabled' in (d.flags || {})) enabled.value = d.flags.tavern_enabled !== false
  } catch { /* 读取失败保持默认 */ }
}

async function toggle() {
  if (busy.value) return
  busy.value = true
  msg.value = ''
  try {
    const r = await apiFetch('/api/flags', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'tavern_enabled', value: !enabled.value }),
    })
    const d = await r.json()
    if (!r.ok || !d.ok) throw new Error(d.error || '保存失败')
    enabled.value = !enabled.value
    msg.value = enabled.value ? '✓ 已开启：她在酒馆里待命' : '已关闭：/api/tavern/* 端点拒绝一切请求'
  } catch (e: unknown) {
    msg.value = '✗ ' + ((e as Error).message || e)
  } finally {
    busy.value = false
  }
}

watch(() => props.show, (v) => { if (v) { msg.value = ''; void load() } })
</script>

<template>
  <div v-if="show" class="tavern-mask" @click.self="emit('close')">
    <section class="tavern glass-strong" role="dialog" aria-modal="true" aria-label="酒馆同玩">
      <header class="t-head">
        <div>
          <span class="eyebrow">TAVERN COMPANION</span>
          <h2>酒馆同玩</h2>
        </div>
        <button class="close" aria-label="关闭酒馆同玩" @click="emit('close')">×</button>
      </header>

      <p class="t-lead">带{{ props.personaName || '她' }}去 SillyTavern 客串一局：扩展在桌上点名她，她按自己的性格与真实状态接一句台词；一局演完，剧情沉淀为她的<b>真回忆</b>——之后日常聊天里聊到就能自然想起。</p>

      <ol class="t-steps">
        <li>本机装好 <b>SillyTavern</b>，并安装「菟菚同伴」扩展（扩展在 ST 侧）。</li>
        <li>打开下方开关——它放行本机的 /api/tavern 接口（仅回环，不暴露到局域网）。</li>
        <li>在 ST 的剧情里点名她；收局后点扩展里的「沉淀」，这一局就成了她的回忆。</li>
      </ol>

      <div class="srow">
        <label>允许酒馆同玩（/api/tavern 接口开关）</label>
        <input type="checkbox" aria-label="允许酒馆同玩" :checked="enabled" :disabled="busy" @change="toggle" />
      </div>
      <p v-if="msg" class="t-msg" :class="{ err: msg.startsWith('✗') }" :role="msg.startsWith('✗') ? 'alert' : 'status'">{{ msg }}</p>

      <p class="t-note">她只带性格、心情与记忆入场，不读你的酒馆存档；卡与世界书只是场景素材。</p>
    </section>
  </div>
</template>

<style scoped>
.tavern-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8, 10, 16, .58); backdrop-filter: blur(5px); }
.tavern { width: min(460px, 94vw); height: 100%; padding: 26px 24px; overflow-y: auto; display: flex; flex-direction: column; color: var(--text); background: linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -20px 0 55px rgba(0,0,0,.25); }
.t-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.t-head h2 { font-size: 1.1rem; font-weight: 700; }
.eyebrow { font-size: .66rem; letter-spacing: 2px; color: var(--text-faint); }
.close { border: none; background: none; color: var(--text-faint); font-size: 1.2rem; cursor: pointer; }
.close:hover { color: var(--text); }
.t-lead { font-size: .86rem; line-height: 1.8; color: var(--text-dim); }
.t-steps { margin: 14px 0; padding-left: 20px; display: flex; flex-direction: column; gap: 8px; font-size: .82rem; line-height: 1.7; color: var(--text-dim); }
.srow { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; font-size: .84rem; color: var(--text-dim); }
.t-msg { font-size: .78rem; margin-top: 6px; color: var(--primary-text); }
.t-msg.err { color: var(--danger, #e08a6d); }
.t-note { margin-top: auto; padding-top: 14px; font-size: .74rem; color: var(--text-faint); line-height: 1.7; }
</style>
