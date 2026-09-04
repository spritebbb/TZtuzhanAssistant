<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getDashboard, type DashboardPoint, type DashboardSummary } from '../api/dashboard'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const dashboard = ref<DashboardSummary | null>(null)
const range = ref<7 | 30 | 90>(30)
const loading = ref(false)
const error = ref('')
let loadSequence = 0

const chartWidth = 680
const chartHeight = 180
const chartPadX = 28
const chartPadY = 18

function seriesPoints(key: 'affection' | 'mood'): string {
  const rows = dashboard.value?.timeline ?? []
  if (!rows.length) return ''
  const usableWidth = chartWidth - chartPadX * 2
  const usableHeight = chartHeight - chartPadY * 2
  return rows.map((row, index) => {
    const x = chartPadX + (rows.length === 1 ? usableWidth / 2 : index / (rows.length - 1) * usableWidth)
    const value = Math.max(0, Math.min(100, row[key]))
    const y = chartPadY + (100 - value) / 100 * usableHeight
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

const affectionPoints = computed(() => seriesPoints('affection'))
const moodPoints = computed(() => seriesPoints('mood'))
const maxMessages = computed(() => Math.max(1, ...(dashboard.value?.timeline.map(row => row.messages) ?? [1])))
const dateLabels = computed(() => {
  const rows = dashboard.value?.timeline ?? []
  if (!rows.length) return []
  const indexes = [...new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1])]
  return indexes.map(index => ({
    text: rows[index].date.slice(5).replace('-', '/'),
    x: chartPadX + (rows.length === 1 ? 0 : index / (rows.length - 1) * (chartWidth - chartPadX * 2)),
  }))
})

function compact(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`
  return String(n)
}

function heatStyle(point: DashboardPoint): Record<string, string> {
  const strength = point.messages === 0 ? 5 : 18 + Math.round(point.messages / maxMessages.value * 72)
  return { background: `color-mix(in srgb, var(--accent) ${strength}%, var(--bg-hover))` }
}

function dueLabel(value: string): string {
  if (!value) return '等一个合适的时候'
  const now = new Date()
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  if (value < today) return `原定 ${value.slice(5)} · 待跟进`
  if (value === today) return '今天该问问了'
  return value.slice(5).replace('-', '月') + '日'
}

async function load() {
  const sequence = ++loadSequence
  loading.value = true
  error.value = ''
  try {
    const result = await getDashboard(range.value)
    if (sequence === loadSequence) dashboard.value = result
  } catch {
    if (sequence === loadSequence) error.value = '总览暂时打不开，过会儿再看'
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

function setRange(value: 7 | 30 | 90) {
  if (range.value === value && dashboard.value) return
  range.value = value
  void load()
}

watch(() => props.show, show => { if (show) void load() })
</script>

<template>
  <div v-if="show" class="dashboard-mask" @click.self="emit('close')">
    <section class="dashboard-panel" role="dialog" aria-modal="true" aria-label="成长总览">
      <header>
        <div>
          <span class="eyebrow">GROWTH OVERVIEW</span>
          <h2>成长总览</h2>
          <p>看看这段关系是怎么一点点长出来的</p>
        </div>
        <div class="header-actions">
          <div class="ranges" aria-label="统计范围">
            <button v-for="value in ([7, 30, 90] as const)" :key="value" :class="{ active: range === value }" @click="setRange(value)">{{ value }}天</button>
          </div>
          <button class="close" title="关闭" @click="emit('close')">×</button>
        </div>
      </header>

      <div class="content">
        <p v-if="loading && !dashboard" class="empty">正在把散落的日子捡起来…</p>
        <p v-else-if="error" class="empty error">{{ error }}</p>
        <template v-else-if="dashboard">
          <div class="current-grid">
            <article class="current-card affection-card">
              <span>好感度</span>
              <strong>{{ dashboard.current.affection.value }}</strong>
              <em>{{ dashboard.current.affection.bond || dashboard.current.affection.stage }}</em>
            </article>
            <article class="current-card mood-card">
              <span>心情</span>
              <strong>{{ dashboard.current.mood.value }}</strong>
              <em>{{ dashboard.current.mood.label }}</em>
            </article>
            <article class="current-card energy-card">
              <span>精力</span>
              <strong>{{ dashboard.current.energy }}</strong>
              <em>{{ dashboard.current.resting ? '正在休息' : '陪伴中' }}</em>
            </article>
            <article class="current-card promise-card">
              <span>未完约定</span>
              <strong>{{ dashboard.current.pending_promises }}</strong>
              <em>她还记着</em>
            </article>
          </div>

          <article class="section chart-section">
            <div class="section-head">
              <div><span class="section-kicker">RELATIONSHIP</span><h3>关系与心情</h3></div>
              <div class="legend"><span class="affection-dot"></span>好感度 <span class="mood-dot"></span>心情</div>
            </div>
            <svg class="trend" :viewBox="`0 0 ${chartWidth} ${chartHeight + 22}`" role="img" aria-label="好感度与心情趋势图">
              <g class="grid-lines">
                <line v-for="value in [0, 25, 50, 75, 100]" :key="value" :x1="chartPadX" :x2="chartWidth - chartPadX" :y1="chartPadY + (100 - value) / 100 * (chartHeight - chartPadY * 2)" :y2="chartPadY + (100 - value) / 100 * (chartHeight - chartPadY * 2)" />
              </g>
              <polyline class="mood-line" :points="moodPoints" />
              <polyline class="affection-line" :points="affectionPoints" />
              <text v-for="label in dateLabels" :key="label.text + label.x" class="axis-label" :x="label.x" :y="chartHeight + 12" text-anchor="middle">{{ label.text }}</text>
            </svg>
          </article>

          <article class="section heat-section">
            <div class="section-head">
              <div><span class="section-kicker">COMPANY</span><h3>聊天热力</h3></div>
              <span class="muted">颜色越深，聊得越久</span>
            </div>
            <div class="heatmap" :class="`days-${dashboard.days}`">
              <span
                v-for="point in dashboard.timeline"
                :key="point.date"
                class="heat-cell"
                :style="heatStyle(point)"
                :title="`${point.date} · ${point.messages} 条消息`"
              ></span>
            </div>
          </article>

          <div class="stats-grid">
            <div><strong>{{ dashboard.stats.active_days }}</strong><span>有聊天的日子</span></div>
            <div><strong>{{ dashboard.stats.messages }}</strong><span>来回消息</span></div>
            <div><strong>{{ compact(dashboard.stats.tokens) }}</strong><span>tokens</span></div>
            <div><strong>¥{{ dashboard.stats.cost.toFixed(4) }}</strong><span>这段时间的账</span></div>
            <div><strong>{{ dashboard.stats.diaries }}</strong><span>写下的日记</span></div>
            <div><strong>{{ dashboard.stats.unlocks }}/{{ dashboard.stats.unlock_total }}</strong><span>共同收藏</span></div>
          </div>

          <div class="lower-grid">
            <article class="section compact-section">
              <div class="section-head"><div><span class="section-kicker">PROMISES</span><h3>还记着的约定</h3></div></div>
              <div v-if="dashboard.promises.length" class="promise-list">
                <div v-for="item in dashboard.promises" :key="item.id" class="promise-row">
                  <span class="promise-mark"></span>
                  <div><strong>{{ item.content }}</strong><small>{{ dueLabel(item.follow_up) }}</small></div>
                </div>
              </div>
              <p v-else class="sub-empty">暂时没有没做完的约定</p>
            </article>

            <article class="section compact-section">
              <div class="section-head"><div><span class="section-kicker">MOMENTS</span><h3>最近的靠近</h3></div></div>
              <div v-if="dashboard.recent_affection.length" class="moment-list">
                <div v-for="item in dashboard.recent_affection" :key="item.ts + item.reason" class="moment-row">
                  <span :class="{ negative: item.delta < 0 }">{{ item.delta > 0 ? '+' : '' }}{{ item.delta }}</span>
                  <div><strong>{{ item.reason }}</strong><small>{{ item.ts.slice(5, 16).replace('T', ' ') }}</small></div>
                  <em>{{ item.value }}</em>
                </div>
              </div>
              <p v-else class="sub-empty">还没有好感变化记录</p>
            </article>
          </div>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dashboard-mask { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: flex-end; background: rgba(8, 10, 16, .62); backdrop-filter: blur(6px); }
.dashboard-panel { width: min(860px, 97vw); height: 100%; padding: 26px 28px; overflow: hidden; display: flex; flex-direction: column; color: var(--text); background: radial-gradient(circle at 78% 4%, color-mix(in srgb, var(--accent) 13%, transparent), transparent 34%), linear-gradient(155deg, var(--bg-card), var(--bg-main)); border-left: 1px solid var(--border); box-shadow: -24px 0 65px rgba(0,0,0,.3); }
header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; flex-shrink: 0; }
.eyebrow, .section-kicker { color: var(--accent); font-size: 10px; letter-spacing: .19em; }
h2 { margin: 5px 0 3px; font-size: 26px; font-weight: 600; }
header p { margin: 0; color: var(--text-muted); font-size: 12px; }
.header-actions { display: flex; align-items: center; gap: 14px; }
.ranges { display: flex; padding: 3px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg-hover); }
.ranges button { border: 0; padding: 5px 9px; border-radius: 7px; color: var(--text-muted); background: transparent; font-size: 11px; cursor: pointer; }
.ranges button.active { color: var(--bg-main); background: var(--accent); }
.close { border: 0; color: var(--text-muted); background: transparent; font-size: 28px; cursor: pointer; }
.content { margin-top: 20px; overflow-y: auto; padding: 1px 4px 42px 0; }
.current-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.current-card { position: relative; min-height: 92px; padding: 13px 15px; overflow: hidden; display: grid; grid-template-columns: 1fr auto; align-items: end; border: 1px solid var(--border); border-radius: 16px; background: color-mix(in srgb, var(--bg-card) 89%, transparent); }
.current-card::after { content: ''; position: absolute; width: 72px; height: 72px; right: -24px; top: -28px; border-radius: 50%; background: color-mix(in srgb, var(--accent) 14%, transparent); }
.current-card span { grid-column: 1 / -1; align-self: start; color: var(--text-muted); font-size: 11px; }
.current-card strong { font-size: 30px; line-height: 1; font-weight: 600; }
.current-card em { color: var(--accent); font-size: 11px; font-style: normal; }
.mood-card::after { background: rgba(240, 184, 108, .14); }
.energy-card::after { background: rgba(100, 180, 154, .15); }
.promise-card::after { background: rgba(170, 139, 205, .15); }
.section { margin-top: 12px; padding: 16px 18px; border: 1px solid var(--border); border-radius: 17px; background: color-mix(in srgb, var(--bg-card) 86%, transparent); }
.section-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
h3 { margin: 2px 0 0; font-size: 14px; font-weight: 550; }
.legend, .muted { color: var(--text-muted); font-size: 10px; }
.legend { display: flex; align-items: center; gap: 6px; }
.affection-dot, .mood-dot { width: 7px; height: 7px; border-radius: 50%; background: #e78aa5; }
.mood-dot { margin-left: 7px; background: #e5b55f; }
.trend { display: block; width: 100%; height: auto; margin-top: 8px; overflow: visible; }
.grid-lines line { stroke: var(--border); stroke-width: 1; opacity: .68; }
.affection-line, .mood-line { fill: none; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.affection-line { stroke: #e78aa5; }
.mood-line { stroke: #e5b55f; opacity: .9; }
.axis-label { fill: var(--text-muted); font-size: 10px; }
.heatmap { display: grid; grid-template-columns: repeat(30, minmax(8px, 1fr)); gap: 4px; margin-top: 14px; }
.heatmap.days-7 { grid-template-columns: repeat(7, minmax(18px, 1fr)); }
.heatmap.days-90 { grid-template-columns: repeat(30, minmax(8px, 1fr)); }
.heat-cell { aspect-ratio: 1; min-height: 8px; border: 1px solid color-mix(in srgb, var(--border) 60%, transparent); border-radius: 4px; transition: transform .15s; }
.heat-cell:hover { transform: scale(1.22); }
.stats-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.stats-grid > div { min-width: 0; padding: 11px 10px; display: flex; flex-direction: column; gap: 3px; text-align: center; border: 1px solid var(--border); border-radius: 13px; background: color-mix(in srgb, var(--bg-card) 82%, transparent); }
.stats-grid strong { font-size: 15px; font-weight: 600; color: var(--accent); overflow: hidden; text-overflow: ellipsis; }
.stats-grid span { color: var(--text-muted); font-size: 10px; }
.lower-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.compact-section { min-height: 180px; }
.promise-list, .moment-list { margin-top: 10px; }
.promise-row, .moment-row { display: flex; align-items: center; gap: 9px; min-width: 0; padding: 8px 0; border-bottom: 1px solid color-mix(in srgb, var(--border) 65%, transparent); }
.promise-row:last-child, .moment-row:last-child { border-bottom: 0; }
.promise-mark { width: 7px; height: 7px; flex-shrink: 0; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 12%, transparent); }
.promise-row div, .moment-row div { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 2px; }
.promise-row strong, .moment-row strong { overflow: hidden; color: var(--text); font-size: 11px; font-weight: 500; white-space: nowrap; text-overflow: ellipsis; }
.promise-row small, .moment-row small { color: var(--text-muted); font-size: 9px; }
.moment-row > span { min-width: 27px; color: #66bd91; font-size: 11px; font-weight: 650; }
.moment-row > span.negative { color: #df7d86; }
.moment-row em { color: var(--text-muted); font-size: 10px; font-style: normal; }
.sub-empty, .empty { color: var(--text-muted); text-align: center; }
.sub-empty { padding: 32px 0 16px; font-size: 11px; }
.empty { padding: 70px 0; }
.empty.error { color: #df7d86; }
@media (max-width: 680px) {
  .dashboard-panel { padding: 20px 16px; }
  header { flex-direction: column; }
  .header-actions { width: 100%; justify-content: space-between; }
  .current-grid { grid-template-columns: 1fr 1fr; }
  .stats-grid { grid-template-columns: repeat(3, 1fr); }
  .lower-grid { grid-template-columns: 1fr; }
  .heatmap, .heatmap.days-90 { grid-template-columns: repeat(15, minmax(8px, 1fr)); }
}
</style>
