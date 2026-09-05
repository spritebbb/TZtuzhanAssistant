<script setup lang="ts">
import { ref, watch } from 'vue'
import { deleteFact, getFacts, updateFact, type FactItem } from '../api/memory'

const props = defineProps<{ show: boolean; personaName?: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const facts = ref<FactItem[]>([])
const loading = ref(false)
const error = ref('')
const editingId = ref<number | null>(null)
const editingText = ref('')
const busyId = ref<number | null>(null)

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
    if (target) target.content = content
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

watch(() => props.show, (show) => { if (show) void load() })
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
      <div class="entries">
        <p v-if="loading" class="empty">正在翻看{{ props.personaName || '助手' }}的记忆…</p>
        <p v-else-if="error" class="empty">{{ error }}</p>
        <template v-else>
          <article v-for="fact in facts" :key="fact.id">
            <template v-if="editingId === fact.id">
              <textarea v-model="editingText" rows="2" maxlength="100"></textarea>
              <div class="actions">
                <button class="primary" :disabled="busyId === fact.id" @click="saveEdit(fact.id)">保存</button>
                <button @click="editingId = null">取消</button>
              </div>
            </template>
            <template v-else>
              <p>{{ fact.content }}</p>
              <div class="meta">
                <time>{{ fact.ts.slice(0, 10) }}</time>
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
.meta { display: flex; justify-content: space-between; align-items: center; color: var(--text-muted); font-size: 12px; }
.actions { display: flex; gap: 8px; }
.actions button { padding: 4px 10px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: transparent; font-size: 12px; cursor: pointer; }
.actions button:hover { border-color: var(--accent); }
.actions button.primary { color: var(--accent); border-color: var(--accent); }
.actions button.danger:hover { color: #e0705a; border-color: #e0705a; }
textarea { width: 100%; box-sizing: border-box; margin-bottom: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 10px; color: var(--text); background: var(--bg-main); font: inherit; resize: vertical; }
.empty { color: var(--text-muted); text-align: center; padding: 40px 0; }
</style>
