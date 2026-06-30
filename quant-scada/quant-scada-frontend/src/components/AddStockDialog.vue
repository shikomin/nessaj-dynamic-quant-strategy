<template>
  <Modal
    :visible="visible"
    title="添加自选股"
    width="540px"
    max-height="540px"
    @close="$emit('close')"
  >
    <div class="filter-row">
      <div class="sub-market-select">
        <label
          v-for="m in subMarkets"
          :key="m.value"
          class="market-radio"
          :class="{ active: subMarket === m.value }"
        >
          <input type="radio" :value="m.value" v-model="subMarket" @change="onSearch" />
          {{ m.label }}
        </label>
      </div>
    </div>
    <div class="search-box">
      <input
        ref="searchInput"
        v-model="keyword"
        type="text"
        placeholder="输入代码或名称搜索..."
        @input="onSearch"
      />
    </div>
    <div v-if="searching" class="search-loading">搜索中...</div>
    <div v-else-if="keyword && results.length === 0" class="search-empty">无匹配结果</div>
    <div class="search-results" v-else-if="results.length > 0">
      <div
        class="search-item"
        v-for="item in results"
        :key="item.code"
        :class="{ added: isAdded(item.code) }"
      >
        <div class="item-info">
          <span class="item-code">{{ item.code }}</span>
          <span class="item-name">{{ item.name }}</span>
          <span class="item-tag sub">{{ marketLabel(item.subMarket) }}</span>
          <span class="item-tag sector" v-if="item.sector">{{ marketLabel(item.sector) }}</span>
        </div>
        <button
          class="btn-add"
          :disabled="isAdded(item.code) || adding === item.code"
          @click="handleAdd(item)"
        >
          {{ isAdded(item.code) ? '已添加' : adding === item.code ? '...' : '+ 添加' }}
        </button>
      </div>
    </div>
  </Modal>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useWatchlistStore } from '@/stores/watchlist'
import { marketLabel } from '@/utils/market'
import Modal from './Modal.vue'
import type { StockSearchItem } from '@/api/watchlist'

const props = defineProps<{
  visible: boolean
  addedCodes: string[]
}>()

const emit = defineEmits<{
  close: []
  added: []
}>()

const store = useWatchlistStore()
const keyword = ref('')
const subMarket = ref('')
const results = ref<StockSearchItem[]>([])
const searching = ref(false)
const adding = ref('')
const searchInput = ref<HTMLInputElement | null>(null)

const subMarkets = [
  { value: '', label: '全部' },
  { value: 'SH', label: '沪市' },
  { value: 'SZ', label: '深市' },
  { value: 'BJ', label: '北交所' }
]

let timer: ReturnType<typeof setTimeout> | null = null

function isAdded(code: string) {
  return props.addedCodes.includes(code)
}

function onSearch() {
  if (timer) clearTimeout(timer)
  if (!keyword.value.trim()) {
    results.value = []
    return
  }
  timer = setTimeout(async () => {
    searching.value = true
    try {
      results.value = await store.searchStocks(keyword.value.trim(), subMarket.value)
    } finally {
      searching.value = false
    }
  }, 300)
}

async function handleAdd(item: StockSearchItem) {
  if (isAdded(item.code)) return
  adding.value = item.code
  try {
    await store.addStock(item.code)
    emit('added')
    results.value = results.value.map(r =>
      r.code === item.code ? { ...r } : r
    )
  } finally {
    adding.value = ''
  }
}

watch(() => props.visible, (v) => {
  if (v) {
    keyword.value = ''
    subMarket.value = ''
    results.value = []
    nextTick(() => searchInput.value?.focus())
  }
})
</script>

<style scoped>
.filter-row {
  margin-bottom: 12px;
}

.sub-market-select {
  display: flex;
  gap: 4px;
}

.market-radio {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  font-size: 12px;
  color: var(--text-secondary);
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all 0.15s;
}

.market-radio input {
  display: none;
}

.market-radio:hover {
  color: var(--text-primary);
  background-color: var(--bg-overlay);
}

.market-radio.active {
  color: var(--text-on-accent);
  background-color: var(--accent-info);
  border-color: var(--accent-info);
}

.search-box input {
  width: 100%;
  padding: 8px 12px;
  background-color: var(--input-bg);
  border: 1px solid var(--input-border);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-size: 14px;
  outline: none;
}

.search-box input:focus {
  border-color: var(--input-focus-border);
}

.search-box input::placeholder {
  color: var(--text-muted);
}

.search-loading, .search-empty {
  text-align: center;
  padding: 24px;
  font-size: 13px;
  color: var(--text-muted);
}

.search-results {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.search-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: var(--radius);
  transition: background-color 0.15s;
}

.search-item:hover {
  background-color: var(--bg-tertiary);
}

.search-item.added {
  opacity: 0.5;
}

.item-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.item-code {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.item-name {
  font-size: 13px;
  color: var(--text-primary);
}

.item-tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
}

.item-tag.sub {
  color: var(--accent-info);
  background-color: var(--accent-info-subtle);
}

.item-tag.sector {
  color: var(--accent-success);
  background-color: var(--accent-success-subtle);
}

.btn-add {
  padding: 4px 14px;
  font-size: 12px;
  font-weight: 500;
  background-color: var(--btn-primary-bg);
  color: var(--btn-primary-text);
  border-radius: var(--radius);
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}

.btn-add:hover:not(:disabled) {
  background-color: var(--btn-primary-hover);
}

.btn-add:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
