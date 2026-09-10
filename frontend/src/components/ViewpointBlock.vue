<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  generateActivityViewpointDraft,
  getActivityViewpoints,
  saveActivityViewpoint,
  type ActivityViewpoint,
  type ViewpointRole,
} from '../api/activities'

const props = defineProps<{ activityId: number; personaName?: string }>()

const viewpoints = ref<ActivityViewpoint[]>([])
const available = ref(true)
const busy = ref(false)
const draftBusy = ref(false)
const error = ref('')
const userDraft = ref('')
const tuzhanDraft = ref('')
const tuzhanEditing = ref(false)

function serverView(role: ViewpointRole): string {
  return viewpoints.value.find((item) => item.role === role)?.content ?? ''
}

function syncDrafts() {
  userDraft.value = serverView('user')
  tuzhanDraft.value = serverView('tuzhan')
  tuzhanEditing.value = false
}

async function load() {
  try {
    const payload = await getActivityViewpoints(props.activityId)
    available.value = true
    viewpoints.value = payload.viewpoints
    syncDrafts()
  } catch {
    available.value = false
  }
}

async function save(role: ViewpointRole) {
  if (busy.value) return
  const content = (role === 'user' ? userDraft.value : tuzhanDraft.value).trim()
  error.value = ''
  if (!content) {
    error.value = '要写点什么才能保存'
    return
  }
  busy.value = true
  try {
    viewpoints.value = await saveActivityViewpoint(props.activityId, role, content)
    if (role === 'tuzhan') {
      tuzhanEditing.value = false
    }
    syncDrafts()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '保存没有成功'
  } finally {
    busy.value = false
  }
}

async function draft() {
  if (draftBusy.value) return
  draftBusy.value = true
  error.value = ''
  tuzhanEditing.value = true
  try {
    tuzhanDraft.value = await generateActivityViewpointDraft(props.activityId)
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : '草稿没有生成'
  } finally {
    draftBusy.value = false
  }
}

watch(() => props.activityId, () => { void load() }, { immediate: true })
onMounted(() => { void load() })
</script>

<template>
  <section v-if="available" class="vp-block" aria-label="双方感想">
    <div class="section-title"><span>SHARED REFLECTIONS</span><h3>双方感想</h3></div>
    <div class="vp-cols">
      <div class="vp-col">
        <small>你记得的</small>
        <textarea
          v-model="userDraft"
          rows="2"
          maxlength="2000"
          :aria-label="'你的感想'"
          placeholder="你自己的解释和感受，随时可以改"
        />
        <button class="vp-save" :disabled="busy" @click="save('user')">保存你的感想</button>
      </div>
      <div class="vp-col">
        <small>她记得的</small>
        <p v-if="!tuzhanEditing" class="vp-text">{{ serverView('tuzhan') || '（她还没说）' }}</p>
        <textarea
          v-else
          v-model="tuzhanDraft"
          rows="2"
          maxlength="2000"
          aria-label="她的感想编辑"
        />
        <div class="vp-actions">
          <button v-if="!tuzhanEditing" class="vp-edit" @click="tuzhanEditing = true">写她的版本</button>
          <button class="vp-edit" :disabled="draftBusy" @click="draft()">
            {{ draftBusy ? '她在想…' : '请她想一想' }}
          </button>
          <button v-if="tuzhanEditing" class="vp-save" :disabled="busy" @click="save('tuzhan')">保存她的感想</button>
        </div>
      </div>
    </div>
    <p v-if="error" class="vp-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.vp-block { margin-top: 16px; padding: 12px 14px; border: 1px dashed var(--border); border-radius: 14px; }
.vp-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 8px; }
.vp-col small { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; color: var(--text-muted); font-size: 10px; }
.vp-col textarea { width: 100%; padding: 6px 9px; border: 1px solid var(--border); border-radius: 10px; background: color-mix(in srgb, var(--bg-main) 70%, transparent); color: var(--text); font-size: 12px; line-height: 1.6; box-sizing: border-box; resize: vertical; }
.vp-text { margin: 0; min-height: 44px; padding: 6px 9px; border-radius: 10px; background: color-mix(in srgb, var(--bg-main) 70%, transparent); color: var(--text); font-size: 12px; line-height: 1.6; white-space: pre-wrap; }
.vp-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.vp-edit, .vp-save { border: 1px solid var(--border); border-radius: 99px; padding: 3px 10px; background: transparent; color: var(--text-muted); font-size: 10px; cursor: pointer; }
.vp-save { border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); color: var(--accent); }
.vp-edit:disabled, .vp-save:disabled { opacity: .5; cursor: default; }
.vp-error { margin: 6px 0 0; color: #d47a7a; font-size: 11px; }
</style>
