<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  getLockStatus,
  initializeKeyslots,
  lockNow,
  unlockLocal,
  unlockRecovery,
  type LockStatus,
} from '../api/lock'

const props = defineProps<{ personaName?: string }>()
const emit = defineEmits<{ (e: 'unlocked'): void; (e: 'status', st: LockStatus): void }>()

const status = ref<LockStatus | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const passphrase = ref('')
const passphraseRepeat = ref('')
const mode = ref<'unlock' | 'init'>('unlock')

const locked = computed(() => status.value?.state === 'locked')
const inactive = computed(() => status.value?.state === 'inactive')
const hasLocalSlot = computed(() => status.value?.slots.local_slot ?? false)
const hasRecoverySlot = computed(() => status.value?.slots.recovery_slot ?? false)
const canInitialize = computed(() =>
  inactive.value && status.value !== null && !status.value.slots.initialized)

async function refresh(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    status.value = await getLockStatus()
    emit('status', status.value)
    if (status.value.state === 'unlocked') emit('unlocked')
    if (canInitialize.value) mode.value = 'init'
  } catch {
    error.value = '锁状态读取失败，请确认后端在运行'
  } finally {
    loading.value = false
  }
}

async function doUnlockLocal(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await unlockLocal()
    notice.value = ''
    await refresh()
  } catch (e) {
    error.value = (e as Error).message || '解锁失败'
  } finally {
    busy.value = false
  }
}

async function doUnlockRecovery(): Promise<void> {
  if (!passphrase.value) return
  busy.value = true
  error.value = ''
  try {
    await unlockRecovery(passphrase.value)
    passphrase.value = ''
    notice.value = ''
    await refresh()
  } catch (e) {
    error.value = (e as Error).message || '解锁失败'
  } finally {
    busy.value = false
  }
}

async function doInitialize(): Promise<void> {
  if (!passphrase.value || passphrase.value !== passphraseRepeat.value) {
    error.value = '两次输入的恢复口令不一致'
    return
  }
  if (passphrase.value.length < 8) {
    error.value = '恢复口令建议至少 8 个字符（换机时唯一能解开数据的钥匙）'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await initializeKeyslots(passphrase.value, passphraseRepeat.value)
    passphrase.value = ''
    passphraseRepeat.value = ''
    notice.value = '密钥已创建：本机自动解锁，恢复口令请离线抄写保存'
    await refresh()
  } catch (e) {
    error.value = (e as Error).message || '初始化失败'
  } finally {
    busy.value = false
  }
}

async function doLockNow(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await lockNow()
    await refresh()
  } catch (e) {
    error.value = (e as Error).message || '锁定失败'
  } finally {
    busy.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="lock-mask" role="dialog" aria-modal="true" aria-label="应用锁定">
    <section class="lock-panel">
      <span class="eyebrow">APP LOCK</span>
      <h2>{{ locked ? '已锁定' : '数据保护' }}</h2>

      <p v-if="loading">正在确认锁状态…</p>
      <template v-else-if="locked">
        <p class="hint">{{ props.personaName || '她' }}的数据已上锁——解锁后才能继续。</p>
        <button v-if="hasLocalSlot" class="primary" :disabled="busy" @click="doUnlockLocal">
          {{ busy ? '解锁中…' : '本机解锁' }}
        </button>
        <form v-if="hasRecoverySlot" class="recovery" @submit.prevent="doUnlockRecovery">
          <label for="lock-passphrase">或用恢复口令</label>
          <input
            id="lock-passphrase"
            v-model="passphrase"
            type="password"
            autocomplete="off"
            placeholder="恢复口令"
            :disabled="busy"
          />
          <button type="submit" :disabled="busy || !passphrase">解锁</button>
        </form>
      </template>
      <template v-else-if="canInitialize && mode === 'init'">
        <p class="hint">
          还没有启用数据保护。创建一把恢复口令后：本机一键解锁，
          换电脑时用恢复口令。<b>口令丢失将无法找回</b>——请抄写在纸上离线保存。
        </p>
        <form class="recovery" @submit.prevent="doInitialize">
          <label for="init-passphrase">设置恢复口令（至少 8 字符）</label>
          <input
            id="init-passphrase"
            v-model="passphrase"
            type="password"
            autocomplete="new-password"
            placeholder="恢复口令"
            :disabled="busy"
          />
          <label for="init-passphrase-repeat">再输入一次确认</label>
          <input
            id="init-passphrase-repeat"
            v-model="passphraseRepeat"
            type="password"
            autocomplete="new-password"
            placeholder="重复口令"
            :disabled="busy"
          />
          <button type="submit" class="primary" :disabled="busy || !passphrase">创建密钥</button>
        </form>
      </template>
      <template v-else>
        <p class="hint">已解锁{{ status?.data_encrypted ? '（数据已加密）' : '。数据加密迁移尚未执行，应用锁当前只锁界面与接口' }}</p>
        <button class="ghost" :disabled="busy" @click="doLockNow">立即锁定</button>
      </template>

      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </section>
  </div>
</template>

<style scoped>
.lock-mask { position: fixed; inset: 0; z-index: 2000; display: grid; place-items: center; background: rgba(8, 10, 16, .82); backdrop-filter: blur(8px); }
.lock-panel { width: min(380px, 92vw); padding: 28px 26px; border: 1px solid var(--border); border-radius: 16px; color: var(--text); background: var(--bg-card); box-shadow: 0 24px 70px rgba(0,0,0,.4); }
.eyebrow { color: var(--accent); font-size: 10px; letter-spacing: .19em; }
h2 { margin: 6px 0 12px; font-size: 22px; }
.hint { margin: 0 0 16px; color: var(--text-muted); font-size: 13px; line-height: 1.7; }
.recovery { display: grid; gap: 8px; margin-top: 14px; }
.recovery label { font-size: 12px; color: var(--text-muted); }
.recovery input { box-sizing: border-box; padding: 9px 12px; border: 1px solid var(--border); border-radius: 9px; outline: none; color: var(--text); background: var(--bg-main); font: inherit; }
.recovery input:focus { border-color: var(--accent); }
button { padding: 9px 16px; border-radius: 9px; border: 1px solid var(--border); background: transparent; color: var(--text); cursor: pointer; font: inherit; }
button.primary { border-color: var(--accent); background: var(--accent); color: #fff; }
button.ghost { color: var(--accent); border-color: var(--accent); }
button:disabled { opacity: .55; cursor: not-allowed; }
.notice { margin: 12px 0 0; font-size: 12px; color: var(--accent); }
.error { margin: 12px 0 0; font-size: 12px; color: #e0705a; }
</style>
