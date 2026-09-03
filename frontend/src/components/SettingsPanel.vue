<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { apiFetch } from '../api'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

interface ConfigData {
  llm_base_url?: string
  llm_model?: string
  llm_temperature?: number
  llm_max_tokens?: number
  llm_api_key_masked?: string
  search_enabled?: boolean
  search_engine?: string
  search_api_key_masked?: string
  image_base_url?: string
  image_model?: string
  image_api_key_masked?: string
  vision_base_url?: string
  vision_model?: string
  vision_api_key_masked?: string
  mood_city?: string
  memory_semantic?: boolean
}

const config = ref<ConfigData>({})
const saveOk = ref(false)
const saving = ref(false)
const saveNote = ref('')

// 响应式表单：打开时用后端当前值初始化；密钥字段留空 = 不修改
const form = ref<Record<string, string | boolean>>({})

// ---- 审计日志 ----
interface AuditRow {
  ts: string
  tool: string
  args: Record<string, string>
  confirmed: string
  ok: boolean
  elapsed_ms: number
  result: string
  error: string
}
const auditLog = ref<AuditRow[]>([])
const auditFilter = ref('')
const auditBusy = ref(false)

async function loadAuditLog() {
  auditBusy.value = true
  try {
    const params = new URLSearchParams()
    if (auditFilter.value) params.set('q', auditFilter.value)
    params.set('limit', '200')
    const r = await apiFetch('/api/audit/log?' + params.toString())
    const d = await r.json()
    if (d.ok) auditLog.value = d.rows || []
  } catch { /* ignore */ }
  finally { auditBusy.value = false }
}

// ---- MCP 服务器管理 ----
interface McpServer {
  name: string
  url: string
  tools_count?: number
}
const mcpServers = ref<McpServer[]>([])
const mcpName = ref('')
const mcpUrl = ref('')
const mcpBusy = ref(false)
const mcpMsg = ref('')

async function loadMcpServers() {
  try {
    const r = await apiFetch('/api/mcp/servers')
    const d = await r.json()
    if (d.ok) mcpServers.value = d.servers || []
  } catch { /* ignore */ }
}

async function addMcpServer() {
  const name = mcpName.value.trim()
  const url = mcpUrl.value.trim()
  if (!name || !url) { mcpMsg.value = '请填写服务器名称和地址'; return }
  mcpBusy.value = true
  mcpMsg.value = ''
  try {
    const r = await apiFetch('/api/mcp/servers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, url }),
    })
    const d = await r.json()
    if (!r.ok || !d.ok) throw new Error(d.error || '注册失败')
    mcpName.value = ''
    mcpUrl.value = ''
    await loadMcpServers()
    mcpMsg.value = '✓ 已注册 ' + name + '（' + (d.server ? '含 ' + (mcpServers.value.find(s => s.name === name)?.tools_count ?? 0) + ' 个工具' : '') + '）'
  } catch (e: unknown) {
    mcpMsg.value = '✗ ' + ((e as Error).message || e)
  } finally {
    mcpBusy.value = false
  }
}

async function removeMcpServer(name: string) {
  if (!confirm('卸载 MCP 服务器「' + name + '」？')) return
  try {
    const r = await apiFetch(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: 'DELETE' })
    const d = await r.json()
    if (d.ok) {
      mcpServers.value = mcpServers.value.filter(s => s.name !== name)
      mcpMsg.value = '✓ 已卸载 ' + name
    }
  } catch (e: unknown) {
    mcpMsg.value = '✗ ' + ((e as Error).message || e)
  }
}

// ---- 插件管理 ----
interface PluginInfo {
  name: string
  display_name: string
  version: string
  description: string
  author: string
  tools: string[]
  routes: string[]
  tasks: string[]
  hooks: Record<string, number>
  loaded: boolean
  disabled: boolean
  error: string
}
const plugins = ref<PluginInfo[]>([])
const pluginBusy = ref('')
const pluginMsg = ref('')
let pluginTimer: number | null = null
const pluginSource = ref('')
const pluginSourceName = ref('')

async function loadPlugins() {
  try {
    const r = await apiFetch('/api/plugins')
    const d = await r.json()
    if (d.ok) plugins.value = d.plugins || []
  } catch { /* ignore */ }
}

// 面板打开期间每 10s 自动刷新（热加载/启停后状态及时可见）
watch(() => props.show, (v) => {
  if (v) {
    pluginTimer = window.setInterval(loadPlugins, 10000)
  } else if (pluginTimer !== null) {
    window.clearInterval(pluginTimer)
    pluginTimer = null
  }
})

async function viewSource(name: string) {
  try {
    const r = await apiFetch(`/api/plugins/${encodeURIComponent(name)}/source`)
    const d = await r.json()
    if (!r.ok || !d.ok) throw new Error(d.error || '读取失败')
    pluginSource.value = d.source
    pluginSourceName.value = name
  } catch (e: unknown) {
    pluginMsg.value = '✗ ' + ((e as Error).message || e)
  }
}

function closeSource() {
  pluginSource.value = ''
  pluginSourceName.value = ''
}

// 拥有写/命令类工具的插件，禁用时二次确认
function hasRiskyTools(p: PluginInfo): boolean {
  return p.tools.some(t => /^(write_file|edit|run_python|run_command|kill_process|activate_window|open_app|clipboard_set|browser_open|codex_run|dsh_run|agent_run|agent_fanout)/.test(t))
}

function pluginStatus(p: PluginInfo): { label: string; cls: string } {
  if (p.disabled) return { label: '已禁用', cls: 'disabled' }
  if (p.loaded) return { label: '已加载', cls: 'ok' }
  return { label: '加载失败', cls: 'fail' }
}

async function pluginAction(name: string, action: 'enable' | 'disable' | 'reload') {
  const target = plugins.value.find(p => p.name === name)
  if (action === 'disable') {
    const risky = target && hasRiskyTools(target)
    const tip = risky
      ? `禁用插件「${target?.display_name || name}」？\n它注册了写/命令类工具（写入、执行命令、本机操控等），禁用后菟菚将失去这些能力，且立即生效。`
      : `禁用插件「${target?.display_name || name}」？其注册的能力会立即卸载。`
    if (!confirm(tip)) return
  }
  pluginBusy.value = name + ':' + action
  pluginMsg.value = ''
  try {
    const r = await apiFetch(`/api/plugins/${encodeURIComponent(name)}/${action}`, { method: 'POST' })
    const d = await r.json()
    if (!r.ok || !d.ok) throw new Error(d.error || '操作失败')
    pluginMsg.value = '✓ ' + name + ' ' + (action === 'enable' ? '已启用' : action === 'disable' ? '已禁用' : '已重载')
  } catch (e: unknown) {
    pluginMsg.value = '✗ ' + ((e as Error).message || e)
  } finally {
    pluginBusy.value = ''
    await loadPlugins()
  }
}

async function open() {
  try {
    const r = await apiFetch('/api/config')
    const d = await r.json()
    if (d.config) {
      config.value = d.config
      const c = d.config
      form.value = {
        llm_base_url: c.llm_base_url || '',
        llm_model: c.llm_model || '',
        llm_temperature: String(c.llm_temperature ?? 0.8),
        llm_max_tokens: String(c.llm_max_tokens ?? 500),
        llm_api_key: '',
        search_enabled: c.search_enabled !== false,
        search_engine: c.search_engine || 'bing',
        search_api_key: '',
        image_base_url: c.image_base_url || '',
        image_model: c.image_model || '',
        image_api_key: '',
        vision_base_url: c.vision_base_url || '',
        vision_model: c.vision_model || '',
        vision_api_key: '',
        mood_city: c.mood_city || '',
        memory_semantic: c.memory_semantic !== false,
      }
    }
  } catch { /* ignore */ }
  await loadMcpServers()
  await loadAuditLog()
  await loadPlugins()
  saveOk.value = false
  saveNote.value = ''
}

async function save() {
  if (saving.value) return
  saving.value = true
  const body: Record<string, string | boolean> = {}
  const f = form.value
  // 文本/数字字段：非空才提交；密钥字段留空表示保持当前值
  const strFields: Array<[string, boolean]> = [
    ['llm_base_url', false], ['llm_model', false],
    ['llm_temperature', false], ['llm_max_tokens', false],
    ['llm_api_key', true], ['search_engine', false],
    ['search_api_key', true], ['image_base_url', false],
    ['image_model', false], ['image_api_key', true],
    ['vision_base_url', false], ['vision_model', false],
    ['vision_api_key', true], ['mood_city', false],
  ]
  for (const [field, isSecret] of strFields) {
    const v = String(f[field] ?? '').trim()
    if (isSecret && !v) continue
    if (!v) continue
    body[field] = v
  }
  body.search_enabled = f.search_enabled ? '1' : '0'
  body.memory_semantic = f.memory_semantic ? '1' : '0'

  try {
    const r = await apiFetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const d = await r.json()
    if (!r.ok || !d.ok) throw new Error(d.error || '保存失败')
    saveOk.value = true
    saveNote.value = '已保存并热重载。记忆/生图相关模型变更建议重启后端后生效。'
    setTimeout(() => saveOk.value = false, 2000)
    // 通知 ToolBar 等组件刷新工具开关状态（联网/天气/生图/识图等可能因配置变化）
    window.dispatchEvent(new CustomEvent('tztuzhan:config-saved'))
  } catch (e: unknown) {
    alert('保存失败：' + ((e as Error).message || e))
  } finally {
    saving.value = false
  }
}

watch(() => props.show, (v) => { if (v) open() })

function fmtTime(ts: string): string {
  try { return ts.slice(5, 19) } catch { return ts }
}

function confirmLabel(c: string): string {
  if (c === 'allow') return '已允许'
  if (c === 'deny') return '已拒绝'
  if (c === 'timeout') return '超时'
  if (c === 'blocked') return '拦截'
  return '自动'
}
</script>

<template>
  <Teleport to="body">
    <div class="overlay" :class="{ show }" @click.self="emit('close')">
      <div class="settings glass-strong">
        <div class="s-head">
          <div class="s-head-left">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
            <span>设置</span>
          </div>
          <button class="s-x" @click="emit('close')">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <div class="s-body">
          <!-- LLM -->
          <div class="sgroup">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2a4 4 0 0 1 4 4v2a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z"/><path d="M8 12h8"/><path d="M10 16h4"/><path d="M3 20h18"/><path d="M12 22v-6"/></svg>
            LLM（对话模型）
          </div>
          <div class="srow"><label>API 地址</label><input v-model="form.llm_base_url" type="text" :placeholder="config.llm_base_url || ''" /></div>
          <div class="srow"><label>模型</label><input v-model="form.llm_model" type="text" :placeholder="config.llm_model || 'deepseek-chat'" /></div>
          <div class="srow"><label>温度</label><input v-model="form.llm_temperature" type="number" step="0.1" min="0" max="2" :placeholder="String(config.llm_temperature ?? 0.8)" /></div>
          <div class="srow"><label>最大 tokens</label><input v-model="form.llm_max_tokens" type="number" min="1" step="50" :placeholder="String(config.llm_max_tokens ?? 500)" /></div>
          <div class="srow"><label>API Key</label><input v-model="form.llm_api_key" type="password" :placeholder="config.llm_api_key_masked || '留空保持当前'" autocomplete="off" /></div>

          <div class="sgroup">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
            联网搜索
          </div>
          <div class="srow"><label>启用搜索</label><input v-model="form.search_enabled" type="checkbox" /></div>
          <div class="srow">
            <label>引擎</label>
            <select v-model="form.search_engine">
              <option value="bing">Bing</option>
              <option value="ddg">DuckDuckGo</option>
              <option value="bocha">博查</option>
            </select>
          </div>
          <div class="srow"><label>搜索 API Key</label><input v-model="form.search_api_key" type="password" :placeholder="config.search_api_key_masked || '留空保持当前'" autocomplete="off" /></div>

          <div class="sgroup">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
            图像生成
          </div>
          <div class="srow"><label>API 地址</label><input v-model="form.image_base_url" type="text" :placeholder="config.image_base_url || ''" /></div>
          <div class="srow"><label>模型</label><input v-model="form.image_model" type="text" :placeholder="config.image_model || 'Qwen/Qwen-Image'" /></div>
          <div class="srow"><label>API Key</label><input v-model="form.image_api_key" type="password" :placeholder="config.image_api_key_masked || '留空保持当前'" autocomplete="off" /></div>

          <div class="sgroup">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            识图（视觉模型）
          </div>
          <div class="srow"><label>API 地址</label><input v-model="form.vision_base_url" type="text" :placeholder="config.vision_base_url || '留空则用 LLM 端点'" /></div>
          <div class="srow"><label>模型</label><input v-model="form.vision_model" type="text" :placeholder="config.vision_model || 'Qwen/Qwen2.5-VL-72B-Instruct'" /></div>
          <div class="srow"><label>API Key</label><input v-model="form.vision_api_key" type="password" :placeholder="config.vision_api_key_masked || '留空保持当前'" autocomplete="off" /></div>

          <div class="sgroup">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            其他
          </div>
          <div class="srow"><label>心情城市</label><input v-model="form.mood_city" type="text" :placeholder="config.mood_city || '留空不查天气'" /></div>
          <div class="srow"><label>语义检索</label><input v-model="form.memory_semantic" type="checkbox" /></div>

          <div class="sgroup">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2l2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4L4.2 7.7l5.4-.8z"/></svg>
            插件
          </div>
          <div v-if="plugins.length === 0" class="mcp-empty">plugins/ 目录暂无插件</div>
          <div v-for="p in plugins" :key="p.name" class="plug-item glass-sub" :class="{ off: p.disabled }">
            <div class="plug-row1">
              <span class="plug-name">{{ p.display_name }}</span>
              <span v-if="p.version" class="plug-ver">v{{ p.version }}</span>
              <span class="plug-status" :class="pluginStatus(p).cls">{{ pluginStatus(p).label }}</span>
              <span class="plug-actions">
                <button class="plug-btn" @click="viewSource(p.name)">源码</button>
                <button v-if="p.disabled" class="plug-btn" :disabled="pluginBusy === p.name + ':enable'" @click="pluginAction(p.name, 'enable')">启用</button>
                <template v-else>
                  <button class="plug-btn" :disabled="pluginBusy === p.name + ':reload'" @click="pluginAction(p.name, 'reload')">重载</button>
                  <button class="plug-btn danger" :disabled="pluginBusy === p.name + ':disable'" @click="pluginAction(p.name, 'disable')">禁用</button>
                </template>
              </span>
            </div>
            <div v-if="p.description" class="plug-desc">{{ p.description }}</div>
            <div class="plug-caps">
              <span v-for="t in p.tools" :key="t" class="arg-chip" title="注册的工具">🔧 {{ t }}</span>
              <span v-for="r in p.routes" :key="r" class="arg-chip" title="HTTP 路由">🌐 {{ r }}</span>
              <span v-for="t in p.tasks" :key="t" class="arg-chip" title="定时任务">⏱ {{ t }}</span>
              <span v-for="(n, k) in p.hooks" :key="k" class="arg-chip" title="钩子">🪝 {{ k }}×{{ n }}</span>
            </div>
            <div v-if="p.error" class="plug-err">{{ p.error }}</div>
          </div>
          <div v-if="pluginMsg" class="mcp-msg" :class="{ err: pluginMsg.startsWith('✗') }">{{ pluginMsg }}</div>
          <div v-if="pluginSource" class="psrc-mask" @click.self="closeSource">
            <div class="psrc-box glass-strong">
              <div class="psrc-head">
                <span>插件源码 · {{ pluginSourceName }}</span>
                <button class="plug-btn" @click="closeSource">关闭</button>
              </div>
              <pre class="psrc-body">{{ pluginSource }}</pre>
            </div>
          </div>

          <div class="sgroup">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/></svg>
            MCP 服务器
          </div>
          <div class="mcp-list">
            <div v-if="mcpServers.length === 0" class="mcp-empty">暂无已注册的外部 MCP 服务器</div>
            <div v-for="s in mcpServers" :key="s.name" class="mcp-item glass-sub">
              <div class="mcp-info">
                <span class="mcp-name">{{ s.name }}</span>
                <span class="mcp-url">{{ s.url }}</span>
                <span v-if="s.tools_count !== undefined" class="mcp-tools">{{ s.tools_count }} 个工具</span>
              </div>
              <button class="mcp-del" title="卸载" @click="removeMcpServer(s.name)">✕</button>
            </div>
          </div>
          <div class="mcp-add">
            <input v-model="mcpName" type="text" placeholder="服务器名称" class="mcp-input" />
            <input v-model="mcpUrl" type="text" placeholder="https://example.com/mcp（仅支持公网 http/https）" class="mcp-input" />
            <button class="mcp-btn" :disabled="mcpBusy" @click="addMcpServer">连接</button>
          </div>
          <div v-if="mcpMsg" class="mcp-msg" :class="{ err: mcpMsg.startsWith('✗') }">{{ mcpMsg }}</div>

          <div class="sgroup">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
            工具审计日志
          </div>
          <div class="audit-bar">
            <input v-model="auditFilter" type="text" placeholder="按工具/结果关键词过滤…" class="audit-input" @keyup.enter="loadAuditLog" />
            <button class="audit-btn" :disabled="auditBusy" @click="loadAuditLog">刷新</button>
          </div>
          <div v-if="auditBusy" class="audit-empty">加载中…</div>
          <div v-else-if="auditLog.length === 0" class="audit-empty">暂无工具调用记录</div>
          <div v-else class="audit-list">
            <div v-for="(row, i) in auditLog" :key="i" class="audit-item glass-sub">
              <div class="audit-row1">
                <span class="audit-time">{{ fmtTime(row.ts) }}</span>
                <span class="audit-tool">{{ row.tool }}</span>
                <span class="audit-confirm" :class="row.confirmed">{{ confirmLabel(row.confirmed) }}</span>
                <span class="audit-ok" :class="{ fail: !row.ok }">{{ row.ok ? '成功' : '失败' }}</span>
                <span class="audit-ms" v-if="row.elapsed_ms">· {{ row.elapsed_ms }}ms</span>
              </div>
              <div v-if="Object.keys(row.args || {}).length" class="audit-args">
                <span v-for="(v, k) in row.args" :key="k" class="arg-chip">{{ k }}={{ v }}</span>
              </div>
              <div class="audit-result" v-if="row.result || row.error">{{ row.error || row.result }}</div>
            </div>
          </div>
        </div>
        <div class="s-foot">
          <span class="saveok" :class="{ show: saveOk }">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="20 6 9 17 4 12"/></svg>
            已保存
          </span>
          <span v-if="saveNote" class="savenote">{{ saveNote }}</span>
          <button class="btn ghost" :disabled="saving" @click="emit('close')">取消</button>
          <button class="btn" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(10, 15, 10, 0.55);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  z-index: 100;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 20px;
  animation: fadeIn 0.2s ease;
}
.overlay.show { display: flex; }
.settings {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  width: min(640px, 96vw);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  animation: popIn 0.25s ease both;
}
.glass-strong {
  backdrop-filter: blur(26px) saturate(1.25);
  -webkit-backdrop-filter: blur(26px) saturate(1.25);
}
.s-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.s-head-left {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
}
.s-head-left svg { color: var(--primary); }
.s-x {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: none;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-faint);
  cursor: pointer;
  transition: all 0.15s ease;
}
.s-x:hover { background: var(--danger-soft); color: var(--danger); }

.s-body { flex: 1; overflow-y: auto; padding: 6px 20px 16px; }
.s-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--border);
}
.sgroup {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 16px 0 6px;
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--primary-text);
  letter-spacing: 0.3px;
  text-transform: uppercase;
}
.sgroup:first-child { margin-top: 4px; }
.sgroup svg { flex-shrink: 0; }
.srow {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 10px 0;
}
.srow label {
  width: 120px;
  flex: 0 0 auto;
  font-size: 0.8rem;
  color: var(--text-dim);
  font-weight: 500;
}
.srow input[type=text], .srow input[type=password], .srow input[type=number], .srow select {
  flex: 1;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  color: var(--text);
  font-size: 0.85rem;
  outline: none;
  min-width: 0;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.srow input:focus, .srow select:focus { border-color: var(--primary); box-shadow: var(--glow); }
.srow input[type=checkbox] { width: 18px; height: 18px; accent-color: var(--primary); cursor: pointer; }
.s-foot .btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--bg-user);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 9px 22px;
  font-size: 0.9rem;
  cursor: pointer;
  font-weight: 600;
  transition: all 0.18s ease;
  box-shadow: 0 3px 10px rgba(124, 154, 85, 0.3);
}
.s-foot .btn:hover { background: var(--bg-user-deep); transform: translateY(-1px); }
.s-foot .btn.ghost { background: transparent; color: var(--text-dim); box-shadow: none; border: 1px solid var(--border); }
.s-foot .btn.ghost:hover { color: var(--text); border-color: var(--primary); transform: none; }
.saveok {
  display: none;
  align-items: center;
  gap: 4px;
  color: var(--primary-text);
  font-size: 0.82rem;
  font-weight: 600;
  margin-right: auto;
  animation: fadeUp 0.2s ease;
}
.saveok.show { display: inline-flex; }
.savenote { font-size: 0.75rem; color: var(--text-faint); margin-right: auto; line-height: 1.4; }
.s-foot .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

.mcp-list { margin: 6px 0 6px; }
.mcp-empty { font-size: 0.8rem; color: var(--text-faint); padding: 6px 0; }
.mcp-item {
  display: flex; align-items: center; gap: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 8px 10px; margin-bottom: 4px;
  transition: border-color 0.15s;
}
.glass-sub {
  background: var(--bg-card);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.mcp-item:hover { border-color: var(--border-light); }
.mcp-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.mcp-name { font-size: 0.85rem; color: var(--primary-text); font-weight: 600; }
.mcp-url { font-size: 0.72rem; color: var(--text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mcp-tools { font-size: 0.75rem; color: var(--primary); }
.mcp-del { background: none; border: none; color: var(--text-faint); cursor: pointer; font-size: 0.9rem; padding: 2px 6px; border-radius: 6px; transition: all 0.15s; }
.mcp-del:hover { color: var(--danger); background: var(--danger-soft); }
.mcp-add { display: flex; gap: 6px; margin: 8px 0; }
.mcp-input { flex: 1; background: var(--bg-input); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 7px 10px; color: var(--text); font-size: 0.82rem; outline: none; min-width: 0; transition: border-color 0.2s; }
.mcp-input:focus { border-color: var(--primary); box-shadow: var(--glow); }
.mcp-btn { background: var(--bg-user); color: #fff; border: none; border-radius: var(--radius-sm); padding: 0 16px; font-size: 0.82rem; cursor: pointer; font-weight: 600; white-space: nowrap; transition: all 0.15s; }
.mcp-btn:hover { background: var(--bg-user-deep); }
.mcp-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.mcp-msg { font-size: 0.78rem; color: var(--primary-text); margin: 4px 0; padding: 6px 10px; background: var(--primary-soft); border-radius: var(--radius-sm); }
.mcp-msg.err { color: var(--danger); background: var(--danger-soft); }

.plug-item { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 10px; margin-bottom: 6px; transition: border-color 0.15s; }
.plug-item:hover { border-color: var(--border-light); }
.plug-item.off { opacity: 0.55; }
.plug-row1 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.plug-name { font-size: 0.85rem; font-weight: 600; color: var(--primary-text); }
.plug-ver { font-size: 0.7rem; color: var(--text-faint); }
.plug-status { font-size: 0.68rem; padding: 1px 8px; border-radius: 10px; }
.plug-status.ok { background: var(--ok-soft); color: var(--ok); }
.plug-status.fail { background: var(--danger-soft); color: var(--danger); }
.plug-status.disabled { background: var(--bg-hover); color: var(--text-faint); }
.plug-actions { margin-left: auto; display: flex; gap: 6px; }
.plug-btn { background: var(--primary-soft); color: var(--primary-text); border: 1px solid transparent; border-radius: var(--radius-sm); padding: 3px 10px; font-size: 0.72rem; cursor: pointer; font-weight: 600; transition: all 0.15s; }
.plug-btn:hover { background: var(--primary); color: var(--text-invert); }
.plug-btn.danger { background: var(--danger-soft); color: var(--danger); }
.plug-btn.danger:hover { background: var(--danger); color: #fff; }
.plug-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.plug-desc { font-size: 0.74rem; color: var(--text-dim); margin-top: 3px; line-height: 1.5; }
.plug-caps { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.plug-err { font-size: 0.72rem; color: var(--danger); margin-top: 4px; word-break: break-all; }

.psrc-mask { position: fixed; inset: 0; background: rgba(10, 15, 10, 0.6); z-index: 200; display: flex; align-items: center; justify-content: center; padding: 20px; }
.psrc-box { border: 1px solid var(--border); border-radius: var(--radius-lg); width: min(760px, 94vw); max-height: 80vh; display: flex; flex-direction: column; box-shadow: var(--shadow-lg); }
.psrc-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; border-bottom: 1px solid var(--border); font-size: 0.9rem; font-weight: 700; color: var(--primary-text); }
.psrc-body { flex: 1; overflow: auto; margin: 0; padding: 12px 16px; font-family: Consolas, 'Courier New', monospace; font-size: 0.78rem; line-height: 1.55; color: var(--text); white-space: pre; tab-size: 4; }

.audit-bar { display: flex; gap: 6px; margin: 6px 0 8px; }
.audit-input { flex: 1; background: var(--bg-input); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 7px 10px; color: var(--text); font-size: 0.82rem; outline: none; min-width: 0; transition: border-color 0.2s; }
.audit-input:focus { border-color: var(--primary); box-shadow: var(--glow); }
.audit-btn { background: var(--bg-user); color: #fff; border: none; border-radius: var(--radius-sm); padding: 0 16px; font-size: 0.82rem; cursor: pointer; font-weight: 600; white-space: nowrap; transition: all 0.15s; }
.audit-btn:hover { background: var(--bg-user-deep); }
.audit-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.audit-empty { font-size: 0.8rem; color: var(--text-faint); padding: 8px 0; }
.audit-list { display: flex; flex-direction: column; gap: 6px; max-height: 300px; overflow-y: auto; }
.audit-item { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 10px; }
.audit-row1 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.audit-time { font-size: 0.7rem; color: var(--text-faint); }
.audit-tool { font-size: 0.82rem; font-weight: 600; color: var(--primary-text); }
.audit-confirm { font-size: 0.68rem; padding: 1px 8px; border-radius: 10px; background: var(--primary-soft); color: var(--primary-text); }
.audit-confirm.allow { background: var(--ok-soft); color: var(--ok); }
.audit-confirm.deny, .audit-confirm.blocked { background: var(--danger-soft); color: var(--danger); }
.audit-confirm.timeout { background: rgba(217, 168, 96, 0.18); color: var(--accent); }
.audit-ok { font-size: 0.7rem; color: var(--ok); }
.audit-ok.fail { color: var(--danger); }
.audit-ms { font-size: 0.68rem; color: var(--text-faint); }
.audit-args { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.arg-chip { font-size: 0.68rem; color: var(--text-dim); background: var(--bg-hover); padding: 1px 6px; border-radius: 4px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.audit-result { font-size: 0.74rem; color: var(--text-dim); margin-top: 4px; word-break: break-all; max-height: 40px; overflow: hidden; }
</style>