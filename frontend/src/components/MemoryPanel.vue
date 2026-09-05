<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  deleteFact,
  deleteUserTerm,
  getFacts,
  getHerProfile,
  getInteractionStyle,
  resetInteractionStyle,
  resolveFactConflict,
  updateFact,
  updateFactSurfacePolicy,
  type FactItem,
  type HerProfileSection,
  type UserTerm,
} from '../api/memory'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const facts = ref<FactItem[]>([])
const loading = ref(false)
const error = ref('')
const editingId = ref<number | null>(null)
const editingText = ref('')
const busyId = ref<number | null>(null)
const profileSections = ref<HerProfileSection[]>([])
const showProfile = ref(false)
const userStyle = ref('')
const userTerms = ref<UserTerm[]>([])

async function load() {
  loading.value = true
  error.value = ''
  try {
    facts.value = await getFacts()
  } catch {
    error.value = '记忆匣子卡住了，过会儿再试'
  } finally {
    loading.value = false
  }
}

async function loadStyle() {
  try {
    const data = await getInteractionStyle()
    userStyle.value = data.style
    userTerms.value = data.terms
  } catch {
    /* 互动偏好读取失败不打扰主列表 */
  }
}

async function loadProfile() {
  if (profileSections.value.length) return
  try {
    profileSections.value = await getHerProfile()
  } catch {
    /* 展示性内容，失败静默 */
  }
}

async function resetStyle() {
  if (!window.confirm('重置自动形成的说话偏好？重置后会随聊天重新慢慢形成')) return
  try {
    await resetInteractionStyle()
    userStyle.value = ''
  } catch {
    error.value = '重置失败，稍后再试'
  }
}

async function removeTerm(term: UserTerm) {
  try {
    await deleteUserTerm(term.id)
    userTerms.value = userTerms.value.filter((t) => t.id !== term.id)
  } catch {
    error.value = '删除失败，稍后再试'
  }
}

function toggleProfile() {
  showProfile.value = !showProfile.value
  if (showProfile.value) void loadProfile()
}

function startEdit(fact: FactItem) {
  editingId.value = fact.id
  editingText.value = fact.content
}

async function saveEdit(id: number) {
  const content = editingText.value.trim()
  if (!content) return
  busyId.value = id
  try {
    await updateFact(id, content)
    const target = facts.value.find((f) => f.id === id)
    if (target) {
      target.content = content
      target.source_type = 'user_correction'
      target.confidence = 1
      target.verified_at = new Date().toISOString()
    }
    editingId.value = null
  } catch {
    error.value = '改写失败，稍后再试'
  } finally {
    busyId.value = null
  }
}

async function remove(id: number) {
  if (!window.confirm(`确定让${props.personaName || '助手'}忘掉这条？删了就真的想不起来了`)) return
  busyId.value = id
  try {
    await deleteFact(id)
    facts.value = facts.value.filter((f) => f.id !== id)
  } catch {
    error.value = '删除失败，稍后再试'
  } finally {
    busyId.value = null
  }
}

function sourceLabel(fact: FactItem) {
  if (fact.source_type === 'user_correction' || fact.verified_at) return '你已确认'
  if (fact.source_type === 'conversation_inference') return '对话提炼'
  return '历史记忆'
}

async function toggleProactive(fact: FactItem) {
  const previous = fact.surface_policy
  const next = previous === 'do_not_proactively_surface' ? 'normal' : 'do_not_proactively_surface'
  fact.surface_policy = next
  busyId.value = fact.id
  try {
    await updateFactSurfacePolicy(fact.id, next)
  } catch {
    fact.surface_policy = previous
    error.value = '呈现设置失败，稍后再试'
  } finally {
    busyId.value = null
  }
}

async function resolveConflict(fact: FactItem, action: 'accept_new' | 'keep_existing') {
  busyId.value = fact.id
  error.value = ''
  try {
    await resolveFactConflict(fact.id, action)
    await load()
  } catch {
    error.value = '确认失败，稍后再试'
  } finally {
    busyId.value = null
  }
}

watch(() => props.show, (show) => { if (show) { void load(); void loadStyle() } }, { immediate: true })
</script>

<template>
  <div v-if="show" class="memory-mask" @click.self="emit('close')">
    <section class="memory-panel" role="dialog" aria-modal="true" :aria-label="(props.personaName || '助手') + '记住的事'">
      <header>
        <div>
          <span class="eyebrow">WHAT SHE REMEMBERS</span>
          <h2>{{ props.personaName || '助手' }}记住的事</h2>
        </div>
        <button class="close" title="关闭" @click="emit('close')">×</button>
      </header>
      <p class="hint">她记错的可以改、可以删——改动立刻生效，下次聊天她就按新的记。</p>
      <div class="tab-row">
        <button :class="{ on: !showProfile }" @click="showProfile = false">她记住的</button>
        <button :class="{ on: showProfile }" @click="toggleProfile">了解{{ props.personaName || '她' }}</button>
      </div>
      <div v-if="showProfile" class="entries">
        <p class="hint small">这些是{{ props.personaName || '她' }}稳定的一面，慢慢相处你会越来越熟</p>
        <article v-for="section in profileSections" :key="section.key" class="profile-card">
          <small>{{ section.label }}</small>
          <p v-for="item in section.items" :key="item">· {{ item }}</p>
        </article>
        <article class="profile-card">
          <small>互动偏好（自动形成，可重置）</small>
          <p v-if="userStyle">· 她习惯对你的说话方式：{{ userStyle }}</p>
          <p v-else>· 说话偏好还没形成，多聊聊就有了</p>
          <p v-for="term in userTerms" :key="term.id">
            · 共同语言「{{ term.term }}」<template v-if="term.meaning">（{{ term.meaning }}）</template>
            <button class="term-del" @click="removeTerm(term)">删</button>
          </p>
          <button v-if="userStyle || userTerms.length" class="reset-btn" @click="resetStyle">重置互动偏好</button>
        </article>
      </div>
      <div v-else class="entries">
        <p v-if="loading" class="empty">正在翻看{{ props.personaName || '助手' }}的记忆…</p>
        <p v-else-if="error" class="empty">{{ error }}</p>
        <template v-else>
          <article v-for="fact in facts" :key="fact.id" :class="{ conflict: fact.status === 'pending_confirmation' }">
            <template v-if="fact.status === 'pending_confirmation'">
              <span class="conflict-badge">等你确认</span>
              <p class="conflict-title">{{ props.personaName || '助手' }}发现两条记忆可能冲突</p>
              <div class="fact-choice old">
                <small>原来记得</small>
                <p>{{ fact.conflicting_content || '原记忆已不存在' }}</p>
              </div>
              <div class="fact-choice new">
                <small>这次听到</small>
                <p>{{ fact.content }}</p>
              </div>
              <div class="conflict-actions">
                <button :disabled="busyId === fact.id" @click="resolveConflict(fact, 'keep_existing')">保留原记忆</button>
                <button class="primary" :disabled="busyId === fact.id" @click="resolveConflict(fact, 'accept_new')">采用新记忆</button>
              </div>
            </template>
            <template v-if="editingId === fact.id">
              <textarea v-model="editingText" rows="2" maxlength="100"></textarea>
              <div class="actions">
                <button class="primary" :disabled="busyId === fact.id" @click="saveEdit(fact.id)">保存</button>
                <button @click="editingId = null">取消</button>
              </div>
            </template>
            <template v-else-if="fact.status === 'active'">
              <p>{{ fact.content }}</p>
              <div class="provenance">
                <span>{{ sourceLabel(fact) }}</span>
                <span>置信度 {{ Math.round(fact.confidence * 100) }}%</span>
                <time>{{ fact.verified_at ? `确认于 ${fact.verified_at.slice(0, 10)}` : fact.ts.slice(0, 10) }}</time>
              </div>
              <div class="meta">
                <label class="surface-toggle">
                  <input
                    type="checkbox"
                    :checked="fact.surface_policy === 'do_not_proactively_surface'"
                    :disabled="busyId === fact.id"
                    @change="toggleProactive(fact)"
                  />
                  不主动提起
                </label>
                <span class="actions">
                  <button :disabled="busyId === fact.id" @click="startEdit(fact)">改写</button>
                  <button class="danger" :disabled="busyId === fact.id" @click="remove(fact.id)">忘掉</button>
                </span>
              </div>
            </template>
          </article>
          <p v-if="!facts.length" class="empty">她还没记住什么。多聊聊，她会慢慢记下关于你的事</p>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.memory-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8, 10, 16, .58); backdrop-filter: blur(5px); }
.memory-panel { width: min(540px, 94vw); height: 100%; padding: 26px 24px; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -20px 0 55px rgba(0,0,0,.25); }
header { display: flex; align-items: flex-start; justify-content: space-between; }
.eyebrow { color: var(--accent); font-size: 10px; letter-spacing: .19em; }
h2 { margin: 5px 0 8px; font-size: 24px; font-weight: 600; }
.close { border: 0; color: var(--text-muted); background: transparent; font-size: 28px; cursor: pointer; }
.hint { margin: 0 0 12px; color: var(--text-muted); font-size: 13px; }
.entries { overflow-y: auto; padding: 4px 2px 50px; }
article { margin-bottom: 12px; padding: 14px 16px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
article p { margin: 0 0 8px; line-height: 1.6; }
article.conflict { border-color: color-mix(in srgb, var(--accent) 52%, var(--border)); background: color-mix(in srgb, var(--accent) 7%, var(--bg-card)); }
.conflict-badge { display: inline-block; margin-bottom: 8px; padding: 3px 8px; border-radius: 99px; color: var(--accent); background: color-mix(in srgb, var(--accent) 13%, transparent); font-size: 11px; }
.conflict-title { font-size: 13px; color: var(--text-muted); }
.fact-choice { margin: 8px 0; padding: 9px 11px; border-radius: 10px; background: color-mix(in srgb, var(--bg-main) 72%, transparent); }
.fact-choice small { color: var(--text-muted); }
.fact-choice p { margin: 3px 0 0; }
.fact-choice.new { border-left: 2px solid var(--accent); }
.conflict-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.conflict-actions button { padding: 6px 11px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: transparent; cursor: pointer; }
.conflict-actions button.primary { color: var(--accent); border-color: var(--accent); }
.meta { display: flex; justify-content: space-between; align-items: center; color: var(--text-muted); font-size: 12px; }
.provenance { display: flex; flex-wrap: wrap; gap: 6px 10px; margin: -2px 0 10px; color: var(--text-muted); font-size: 11px; }
.provenance span:first-child { color: var(--accent); }
.surface-toggle { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
.surface-toggle input { accent-color: var(--accent); }
.actions { display: flex; gap: 8px; }
.actions button { padding: 4px 10px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: transparent; font-size: 12px; cursor: pointer; }
.actions button:hover { border-color: var(--accent); }
.actions button.primary { color: var(--accent); border-color: var(--accent); }
.actions button.danger:hover { color: #e0705a; border-color: #e0705a; }
textarea { width: 100%; box-sizing: border-box; margin-bottom: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 10px; color: var(--text); background: var(--bg-main); font: inherit; resize: vertical; }
.empty { color: var(--text-muted); text-align: center; padding: 40px 0; }
.tab-row { display: flex; gap: 8px; margin: 0 0 12px; }
.tab-row button { padding: 6px 14px; border: 1px solid var(--border); border-radius: 99px; color: var(--text-muted); background: transparent; font-size: 12px; cursor: pointer; }
.tab-row button.on { color: var(--accent); border-color: var(--accent); }
.hint.small { margin: 0 0 10px; font-size: 11px; }
.profile-card small { color: var(--accent); font-size: 11px; }
.profile-card p { margin: 5px 0 0; font-size: 13px; line-height: 1.6; }
.term-del { margin-left: 8px; padding: 1px 8px; border: 1px solid var(--border); border-radius: 7px; color: var(--text-muted); background: transparent; font-size: 11px; cursor: pointer; }
.term-del:hover { color: #e0705a; border-color: #e0705a; }
.reset-btn { margin-top: 10px; padding: 5px 12px; border: 1px solid var(--border); border-radius: 8px; color: var(--text-muted); background: transparent; font-size: 12px; cursor: pointer; }
.reset-btn:hover { color: var(--accent); border-color: var(--accent); }
</style>
