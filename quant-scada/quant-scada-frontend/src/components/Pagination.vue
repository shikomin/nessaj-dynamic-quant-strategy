<template>
  <div class="pagination" v-if="total > 0">
    <div class="pagination-info">
      共 {{ total }} 条，第 {{ current + 1 }}/{{ totalPages }} 页
    </div>
    <div class="pagination-controls">
      <select class="page-size" :value="pageSize" @change="onSizeChange">
        <option v-for="s in sizeOptions" :key="s" :value="s">{{ s }} 条/页</option>
      </select>
      <button class="page-btn" :disabled="current <= 0" @click="$emit('change', 0)">首页</button>
      <button class="page-btn" :disabled="current <= 0" @click="$emit('change', current - 1)">上一页</button>
      <button
        v-for="p in visiblePages"
        :key="p"
        class="page-btn"
        :class="{ active: p === current }"
        @click="$emit('change', p)"
      >{{ p + 1 }}</button>
      <button class="page-btn" :disabled="current >= totalPages - 1" @click="$emit('change', current + 1)">下一页</button>
      <button class="page-btn" :disabled="current >= totalPages - 1" @click="$emit('change', totalPages - 1)">末页</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  current: number
  total: number
  pageSize: number
  totalPages: number
}>()

const emit = defineEmits<{
  change: [page: number]
  sizeChange: [size: number]
}>()

const sizeOptions = [10, 20, 30, 50]

function onSizeChange(e: Event) {
  const val = parseInt((e.target as HTMLSelectElement).value)
  emit('sizeChange', val)
}

const visiblePages = computed(() => {
  const pages: number[] = []
  const total = props.totalPages
  const cur = props.current
  let start = Math.max(0, cur - 2)
  let end = Math.min(total - 1, cur + 2)
  if (end - start < 4) {
    if (start === 0) end = Math.min(total - 1, 4)
    else start = Math.max(0, total - 5)
  }
  for (let i = start; i <= end; i++) pages.push(i)
    return pages
})



</script>

<style scoped>
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
}

.pagination-info {
  font-size: 13px;
  color: var(--text-secondary);
}

.pagination-controls {
  display: flex;
  align-items: center;
  gap: 4px;
}

.page-size {
  padding: 4px 8px;
  margin-right: 8px;
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
  cursor: pointer;
}

.page-btn {
  padding: 4px 10px;
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
  min-width: 32px;
  text-align: center;
}

.page-btn:hover:not(:disabled) {
  background-color: var(--bg-overlay);
  color: var(--text-primary);
}

.page-btn.active {
  background-color: var(--accent-info);
  border-color: var(--accent-info);
  color: var(--text-on-accent);
}

.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
