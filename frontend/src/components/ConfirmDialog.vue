<script setup lang="ts">
// NP-09：通用确认弹窗（替代 window.confirm）——与既有"彻底重置"弹窗同款式。
// 危险操作用 danger 红色确认键；Esc/取消/遮罩点击均视为取消。
import { onBeforeUnmount, onMounted } from 'vue'

const props = defineProps<{
  show: boolean
  title: string
  body?: string
  confirmText?: string
  danger?: boolean
}>()
const emit = defineEmits<{ confirm: []; cancel: [] }>()

function onKeydown(e: KeyboardEvent) {
  if (!props.show) return
  if (e.key === 'Escape') emit('cancel')
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div v-if="show" class="cd-mask" @click.self="emit('cancel')">
    <div class="cd-card" role="alertdialog" aria-modal="true" :aria-label="title">
      <div class="cd-title">{{ title }}</div>
      <p v-if="body" class="cd-body">{{ body }}</p>
      <div class="cd-actions">
        <button class="cd-btn ghost" @click="emit('cancel')">取消</button>
        <button class="cd-btn" :class="danger ? 'danger' : 'primary'" @click="emit('confirm')">
          {{ confirmText || '确定' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cd-mask {
  position: fixed;
  inset: 0;
  z-index: 1500;
  background: rgba(8, 10, 16, 0.55);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.cd-card {
  width: min(400px, 92vw);
  background: var(--bg-card);
  border: 1px solid var(--edge-highlight);
  border-radius: var(--radius-lg, 14px);
  box-shadow: 0 22px 60px rgba(0, 0, 0, 0.4);
  padding: 18px 20px;
}
.cd-title {
  font-size: 0.98rem;
  font-weight: 700;
  color: var(--text);
}
.cd-body {
  font-size: 0.84rem;
  color: var(--text-dim);
  line-height: 1.75;
  margin: 8px 0 0;
}
.cd-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
.cd-btn {
  border: none;
  border-radius: var(--radius-md, 10px);
  font: inherit;
  font-weight: 600;
  padding: 7px 16px;
  cursor: pointer;
}
.cd-btn.primary {
  background: linear-gradient(135deg, #be7f9e, #82649f);
  color: #fff;
}
.cd-btn.danger {
  background: var(--danger-soft, rgba(224, 138, 109, 0.16));
  color: var(--danger, #e08a6d);
}
.cd-btn.ghost {
  background: none;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.14));
  color: var(--text-dim);
}
</style>
