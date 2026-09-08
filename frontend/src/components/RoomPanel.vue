<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiFetch } from '../api'

defineProps<{ preferencesOnly?: boolean }>()
interface Item { id: number; title: string; shape: string; x: number; y: number; placed: boolean; hidden: boolean }
interface Preference { id: number; owner: string; category: string; value: string }
const items = ref<Item[]>([])
const preferences = ref<Preference[]>([])
const options = ref<Record<string, string[]>>({})
const enabled = ref(true)
const theme = ref<{ category: string; token: string }[]>([])
const palette: Record<string, string> = { 蓝色: '#91a5b333', 绿色: '#7ca28b33', 暖色: '#d8ab8533', 冷色: '#929dc333', 黑白: '#99999933' }
const roomStyle = computed(() => ({ '--room-wall': palette[theme.value.find(t => t.category === 'color')?.token || ''] || '#91a5b31a' }))
const busy = ref(false)
const error = ref('')
const owner = ref('user')
const category = ref('color')
const value = ref('')
const source = ref('')
const labels: Record<string, string> = { color: '颜色', style: '画风', motif: '意象', layout: '布局' }
const shown = computed(() => items.value.filter(i => i.placed && !i.hidden))

async function load() {
  const r = await apiFetch('/api/memory/aesthetics')
  if (!r.ok) throw new Error('房间暂时无法读取')
  const d = await r.json()
  items.value = d.items || []; preferences.value = d.preferences || []
  options.value = d.options || {}; enabled.value = d.enabled !== false
  theme.value = d.theme || []
}
async function save(path: string, method: string, body?: object) {
  busy.value = true; error.value = ''
  try {
    const r = await apiFetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
    if (!r.ok) throw new Error('保存失败，请刷新后重试')
    await load()
  } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
function putPreference() {
  if (!value.value) return
  save('/api/memory/aesthetics', 'PUT', {
    owner: owner.value, category: category.value, value: value.value,
    ...(owner.value === 'assistant' ? { source_type: 'artifact', source_id: Number(source.value) } : {}),
  })
}
function move(item: Item, dx: number, dy: number, hidden = false) {
  save(`/api/memory/aesthetics/placements/${item.id}`, 'PUT', {
    x: Math.max(0, Math.min(1, item.x + dx)), y: Math.max(0, Math.min(1, item.y + dy)), hidden,
  })
}
onMounted(() => load().catch(e => { error.value = e.message }))
</script>

<template>
  <section class="room-section" :aria-label="preferencesOnly ? '共同审美' : '共同房间'">
    <h3>{{ preferencesOnly ? '共同审美' : '我们的房间' }}</h3>
    <p v-if="!preferencesOnly">把一起完成的作品摆在这里，位置随时可以调整</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="!enabled">共同审美已关闭，仍可整理已经摆放的作品</p>
    <div v-if="!preferencesOnly" class="room-stage" :style="roomStyle" role="img" :aria-label="shown.length ? `房间里有 ${shown.length} 件作品` : '房间还是空的'">
      <p v-if="!shown.length" class="room-empty">房间还是空的，完成活动后可以挑选作品来摆放</p>
      <div v-for="item in shown" :key="item.id" class="room-object" :style="{ left: `${8 + item.x * 84}%`, top: `${15 + item.y * 65}%` }">
        <span class="shape" :class="['plant', 'star', 'stone', 'lamp'].includes(item.shape) ? item.shape : 'box'" />
        <span>{{ item.title }}</span>
      </div>
    </div>
    <ul v-if="!preferencesOnly" class="room-controls">
      <li v-for="item in items" :key="item.id">
        <span>{{ item.title }}</span>
        <button v-if="!item.placed || item.hidden" :disabled="busy || (!enabled && !item.placed)" :aria-label="`摆放${item.title}`" @click="move(item, 0, 0)">摆放</button>
        <template v-else>
          <button :disabled="busy" :aria-label="`左移${item.title}`" @click="move(item, -.1, 0)">←</button>
          <button :disabled="busy" :aria-label="`右移${item.title}`" @click="move(item, .1, 0)">→</button>
          <button :disabled="busy" :aria-label="`上移${item.title}`" @click="move(item, 0, -.1)">↑</button>
          <button :disabled="busy" :aria-label="`下移${item.title}`" @click="move(item, 0, .1)">↓</button>
          <button :disabled="busy" :aria-label="`隐藏${item.title}`" @click="move(item, 0, 0, true)">隐藏</button>
        </template>
      </li>
    </ul>
    <details :open="preferencesOnly">
      <summary>共同审美</summary>
      <section v-for="who in ['user', 'assistant']" :key="who" :aria-label="who === 'user' ? '你的偏好' : '她的偏好'">
        <h4>{{ who === 'user' ? '你的偏好' : '从作品中确认的她的偏好' }}</h4>
        <p v-if="!preferences.some(p => p.owner === who)">还没有确认的偏好</p>
        <p v-for="pref in preferences.filter(p => p.owner === who)" :key="pref.id">
          {{ labels[pref.category] }} · {{ pref.value }}
          <button :disabled="busy" :aria-label="`撤销${who === 'user' ? '你的' : '她的'}${pref.value}偏好`" @click="save(`/api/memory/aesthetics/${pref.id}`, 'DELETE')">撤销</button>
        </p>
      </section>
      <form @submit.prevent="putPreference">
        <label>记录给谁<select v-model="owner"><option value="user">我自己</option><option value="assistant">从作品确认给她</option></select></label>
        <label>类别<select v-model="category" @change="value = ''"><option v-for="(_, key) in options" :key="key" :value="key">{{ labels[key] }}</option></select></label>
        <label>选择<select v-model="value" required><option value="" disabled>请选择</option><option v-for="token in options[category] || []" :key="token">{{ token }}</option></select></label>
        <label v-if="owner === 'assistant'">依据作品<select v-model="source" required><option value="" disabled>请选择作品</option><option v-for="item in items" :key="item.id" :value="String(item.id)">{{ item.title }}</option></select></label>
        <button :disabled="busy || !enabled">确认偏好</button>
      </form>
    </details>
  </section>
</template>

<style scoped>
.room-section { padding: 16px 0; border-bottom: 1px solid #8884; }
.room-section p { font-size: 13px; opacity: .85; }
.room-stage { position: relative; height: 240px; border: 1px solid #8886; border-radius: 14px; overflow: hidden; background: linear-gradient(180deg,var(--room-wall) 75%,#94794c33 75%); }
.room-empty { text-align: center; padding: 70px 20px; }
.room-object { position: absolute; transform: translate(-50%,-50%); display: grid; justify-items: center; max-width: 95px; font-size: 11px; text-align: center; }
.shape { display: block; width: 30px; height: 30px; background: #9a8871; border-radius: 6px; }
.plant { border-radius: 50% 50% 15% 15%; background: #6a9b78; }
.star { background: #d9b861; clip-path: polygon(50% 0,61% 35%,100% 35%,69% 58%,82% 100%,50% 74%,18% 100%,31% 58%,0 35%,39% 35%); }
.lamp { background: #daa977; border-radius: 50%; }
.stone { background: #93a7b7; border-radius: 35% 50% 15% 20%; }
.room-controls { list-style: none; padding: 0; }.room-controls li { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 8px 0; }
.room-controls li > span { flex: 1; min-width: 90px; }
button,select { font: inherit; color: inherit; background: transparent; border: 1px solid #8887; border-radius: 6px; padding: 5px 8px; }
button:focus-visible,select:focus-visible,summary:focus-visible { outline: 2px solid #74aade; outline-offset: 2px; }
button:disabled { opacity: .5; }form { display: flex; flex-wrap: wrap; gap: 10px; }label { display: grid; gap: 4px; font-size: 13px; }
@media(max-width:480px) { .room-stage { height: 200px; }form label { width: 100%; } }
</style>
