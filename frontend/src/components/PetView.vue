<script setup lang="ts">
/**
 * L10：宠物视图（在独立透明窗口的 pet.html 里渲染）。
 *
 * - 复用现有立绘资源（/persona/full/{state} 五档）与 VisualState，不复制人格数据；
 * - 拖动经 tuzhanPet.drag(dx,dy) 走 IPC（主进程按工作区 clamp）；
 * - Escape 关闭；透明区点击穿透：默认穿透，鼠标悬停在立绘上时恢复交互
 *   （P1-9：此前从未开启穿透，260×360 的透明窗口是隐形点击黑洞）；
 * - reduced-motion 适配：呼吸动画关闭。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { portraitMoodFor, type PortraitMood } from '../utils/portrait'

interface PetBridge {
  drag: (delta: { dx: number; dy: number }) => void
  close: () => void
  toggleIgnoreMouse: (ignore: boolean) => void
  onVisibility?: (cb: (v: { hidden: boolean }) => void) => () => void
}

const props = withDefaults(defineProps<{ backendBase?: string }>(), { backendBase: '' })

const bridge = (window as unknown as { tuzhanPet?: PetBridge }).tuzhanPet ?? null
// 合法初值必须是后端五档白名单之一（low/plain/lazy/happy/excited）；
// 旧版 'calm' 不在白名单里，/persona/full/calm 404，立绘永远加载失败（P1-8）
const visualState = ref<PortraitMood>('plain')
const personaName = ref('')
const dragging = ref(false)
let last = { x: 0, y: 0 }
let pollTimer: number | null = null

function extractPersonaName(value: unknown): string {
  if (value && typeof value === 'object') {
    const obj = value as { name?: unknown; id?: unknown }
    return String(obj.name ?? obj.id ?? '')
  }
  return typeof value === 'string' ? value : ''
}

async function refreshMeta(): Promise<void> {
  try {
    const resp = await fetch(`${props.backendBase}/api/meta?session_id=`)
    if (!resp.ok) return
    const data = await resp.json()
    // /api/meta 的 visual_state 是结构化对象（presence.py），立绘档位从
    // mood.value 走与主窗口相同的 portraitMoodFor 映射（P1-8）
    const moodValue = Number(data?.mood?.value ?? 60)
    if (Number.isFinite(moodValue)) {
      visualState.value = portraitMoodFor(moodValue)
    }
    personaName.value = extractPersonaName(data?.persona)
  } catch {
    /* 后端未起时保持上一帧立绘 */
  }
}

function setIgnore(ignore: boolean): void {
  if (!bridge) return
  bridge.toggleIgnoreMouse(ignore)
}

/** 鼠标命中检测：悬在立绘上才接管鼠标，其余区域穿透给桌面。 */
function updateIgnoreOnMove(ev: MouseEvent): void {
  if (!bridge || dragging.value) return
  const el = document.querySelector('.pet-portrait')
  let over = false
  if (el) {
    const r = el.getBoundingClientRect()
    over = ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom
  }
  setIgnore(!over)
}

function onPointerDown(ev: PointerEvent): void {
  if (!bridge) return
  dragging.value = true
  last = { x: ev.clientX, y: ev.clientY }
  bridge.toggleIgnoreMouse(false)
}

function onPointerMove(ev: PointerEvent): void {
  if (!dragging.value || !bridge) return
  bridge.drag({ dx: ev.clientX - last.x, dy: ev.clientY - last.y })
  last = { x: ev.clientX, y: ev.clientY }
}

function onPointerUp(): void {
  if (dragging.value && bridge) bridge.toggleIgnoreMouse(true) // 松手后交还穿透，下一帧 mousemove 会按位置校正
  dragging.value = false
}

function onKeydown(ev: KeyboardEvent): void {
  if (ev.key === 'Escape') bridge?.close()
}

onMounted(() => {
  void refreshMeta()
  pollTimer = window.setInterval(refreshMeta, 60_000)
  window.addEventListener('keydown', onKeydown)
  // 默认整体穿透（窗口其余 260×360 的透明区域不该挡住桌面点击）
  setIgnore(true)
  window.addEventListener('mousemove', updateIgnoreOnMove)
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('mousemove', updateIgnoreOnMove)
})
</script>

<template>
  <div class="pet" :class="{ dragging }" @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointerleave="onPointerUp">
    <img
      class="pet-portrait"
      :src="`${backendBase}/persona/full/${visualState}`"
      :alt="personaName ? `${personaName}的立绘` : '菟菚的立绘'"
      draggable="false"
    />
    <div v-if="!bridge" class="pet-hint">桌面宠物需要桌面版（此预览仅供开发）</div>
  </div>
</template>

<style scoped>
.pet {
  width: 100vw;
  height: 100vh;
  background: transparent;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  user-select: none;
  cursor: grab;
  overflow: hidden;
}
.pet.dragging { cursor: grabbing; }
.pet-portrait {
  height: 96%;
  object-fit: contain;
  filter: drop-shadow(0 8px 18px rgba(0, 0, 0, 0.35));
  animation: pet-breathe 4.2s ease-in-out infinite;
  pointer-events: none; /* 拖拽事件落在容器上，避免图片拦截 */
}
@keyframes pet-breathe {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}
@media (prefers-reduced-motion: reduce) {
  .pet-portrait { animation: none; }
}
.pet-hint {
  position: absolute;
  top: 6px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 11px;
  color: rgba(120, 120, 130, 0.9);
  background: rgba(20, 20, 26, 0.72);
  padding: 3px 8px;
  border-radius: 8px;
  white-space: nowrap;
}
</style>
