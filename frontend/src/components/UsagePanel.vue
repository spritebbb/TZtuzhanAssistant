<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getUsageSummary, type UsageSummary } from '../api/usage'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const summary = ref<UsageSummary | null>(null)
const loading = ref(false)
const error = ref('')

const CHANNEL_LABELS: Record<string, string> = {
  reply: '聊天回复',
  chat: '每日整理',
  perception: '情绪感知',
  tool: '工具调用',
}
const channelLabel = (c: string) => CHANNEL_LABELS[c] ?? c

const maxChannelTokens = computed(() =>
  Math.max(1, ...(summary.value?.by_channel.map((c) => c.prompt + c.completion) ?? [1]))
)

function fmt(n: number): string {
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万` : String(n)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    summary.value = await getUsageSummary(7)
  } catch {
    error.value = '账本暂时打不开，过会儿再看'
  } finally {
    loading.value = false
  }
}

watch(() => props.show, (show) => { if (show) void load() })
</script>

<template>
  <div v-if="show" class="usage-mask" @click.self="emit('close')">
    <section class="usage-panel" role="dialog" aria-modal="true" aria-label="养菟菚的账本">
      <header>
        <div>
          <span class="eyebrow">COST LEDGER</span>
          <h2>养她的账本</h2>
        </div>
        <button class="close" title="关闭" @click="emit('close')">×</button>
      </header>
      <p class="hint">按 tokens 估算的花费（输入 ¥{{ summary?.prices.input_per_mtok ?? 1 }}/百万，输出 ¥{{ summary?.prices.output_per_mtok ?? 2 }}/百万）</p>
      <div class="entries">
        <p v-if="loading" class="empty">正在翻账本…</p>
        <p v-else-if="error" class="empty">{{ error }}</p>
        <template v-else-if="summary">
          <div class="cards">
            <div class="card">
              <span class="label">今天</span>
              <strong>¥{{ summary.today.cost.toFixed(4) }}</strong>
              <span class="sub">{{ fmt(summary.today.prompt + summary.today.completion) }} tokens · {{ summary.today.calls }} 次调用</span>
            </div>
            <div class="card">
              <span class="label">近 {{ summary.days }} 天</span>
              <strong>¥{{ summary.period.cost.toFixed(4) }}</strong>
              <span class="sub">{{ fmt(summary.period.prompt + summary.period.completion) }} tokens · {{ summary.period.calls }} 次调用</span>
            </div>
          </div>
          <h3>花在哪儿了</h3>
          <div v-for="row in summary.by_channel" :key="row.channel" class="bar-row">
            <span class="bar-label">{{ channelLabel(row.channel) }}</span>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: `${Math.max(2, (row.prompt + row.completion) / maxChannelTokens * 100)}%` }"></div>
            </div>
            <span class="bar-value">¥{{ row.cost.toFixed(4) }}</span>
          </div>
          <p v-if="!summary.by_channel.length" class="empty">还没有用量记录，聊几句就有了</p>
          <p class="note" v-if="summary.period.estimated > 0">
            其中 {{ summary.period.estimated }} 次调用是按字符估算的（端点未返回精确用量）
          </p>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.usage-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8, 10, 16, .58); backdrop-filter: blur(5px); }
.usage-panel { width: min(480px, 94vw); height: 100%; padding: 26px 24px; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -20px 0 55px rgba(0,0,0,.25); }
header { display: flex; align-items: flex-start; justify-content: space-between; }
.eyebrow { color: var(--accent); font-size: 10px; letter-spacing: .19em; }
h2 { margin: 5px 0 8px; font-size: 24px; font-weight: 600; }
h3 { margin: 22px 0 10px; font-size: 14px; color: var(--text-muted); font-weight: 500; }
.close { border: 0; color: var(--text-muted); background: transparent; font-size: 28px; cursor: pointer; }
.hint { margin: 0 0 14px; color: var(--text-muted); font-size: 12px; }
.entries { overflow-y: auto; padding-bottom: 40px; }
.cards { display: flex; gap: 12px; }
.card { flex: 1; display: flex; flex-direction: column; gap: 4px; padding: 14px 16px; border: 1px solid var(--border); border-radius: 14px; background: color-mix(in srgb, var(--bg-card) 88%, transparent); }
.card .label { font-size: 12px; color: var(--text-muted); }
.card strong { font-size: 22px; color: var(--accent); }
.card .sub { font-size: 11px; color: var(--text-muted); }
.bar-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.bar-label { width: 60px; flex-shrink: 0; font-size: 12px; color: var(--text-muted); }
.bar-track { flex: 1; height: 10px; border-radius: 5px; background: var(--bg-hover); overflow: hidden; }
.bar-fill { height: 100%; border-radius: 5px; background: var(--accent); transition: width .4s; }
.bar-value { width: 70px; flex-shrink: 0; text-align: right; font-size: 12px; }
.note { margin-top: 14px; color: var(--text-muted); font-size: 11px; }
.empty { color: var(--text-muted); text-align: center; padding: 40px 0; }
</style>
