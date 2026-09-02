<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ensureBaseUrl, getBaseUrl } from '../api'

const props = defineProps<{ size?: number }>()

const imgUrl = ref('')

onMounted(async () => {
  await ensureBaseUrl()
  imgUrl.value = `${getBaseUrl()}/persona`
})
</script>

<template>
  <img
    class="portrait"
    :src="imgUrl"
    alt="菟菚"
    :style="{ width: props.size + 'px', height: props.size + 'px' }"
    @error="imgUrl = ''"
  />
</template>

<style scoped>
.portrait {
  border-radius: var(--radius-md);
  object-fit: cover;
  border: 2px solid #fff;
  box-shadow:
    0 0 0 1px var(--border),
    var(--shadow-sm);
  flex-shrink: 0;
  background: var(--primary-soft);
}
</style>
