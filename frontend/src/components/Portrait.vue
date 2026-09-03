<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ensureBaseUrl, getApiUrl } from '../api'

const props = defineProps<{ size?: number }>()

const imgUrl = ref('')

onMounted(async () => {
  await ensureBaseUrl()
  imgUrl.value = getApiUrl('/persona/cutout', true)
})
</script>

<template>
  <div
    class="portrait-wrap"
    :style="{ width: props.size + 'px', height: props.size + 'px' }"
  >
    <img
      class="portrait"
      :src="imgUrl"
      alt="菟菚"
      @error="imgUrl = ''"
    />
  </div>
</template>

<style scoped>
.portrait-wrap {
  border-radius: var(--radius-md);
  overflow: hidden;
  position: relative;
  flex-shrink: 0;
  background: var(--primary-soft);
  border: 1px solid var(--border-light);
  box-shadow: var(--shadow-sm);
}
.portrait {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 12%;
  display: block;
}
</style>
