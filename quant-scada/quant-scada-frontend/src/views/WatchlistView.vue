<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">自选股</h1>
      <div class="header-actions">
        <div class="search-box">
          <input
            v-model="keyword"
            type="text"
            placeholder="搜索代码或名称..."
            @input="onSearch"
          />
        </div>
        <button class="btn-sort" :class="{ active: sortMode }" @click="toggleSort">
          {{ sortMode ? '完成排序' : '调整顺序' }}
        </button>
        <button class="btn-add" @click="showAddDialog = true">+ 添加自选股</button>
      </div>
    </div>

    <div class="table-wrapper">
      <table class="stock-table">
        <thead>
          <tr>
            <th v-if="sortMode" class="col-drag"></th>
            <th class="col-code">代码</th>
            <th class="col-name">名称</th>
            <th class="col-market">市场</th>
            <th class="col-sector">板块</th>
            <th class="col-time">添加时间</th>
            <th class="col-action">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(item, idx) in store.list"
            :key="item.id"
            :draggable="sortMode"
            :class="{ dragging: sortMode && dragIndex === idx, 'drag-over': sortMode && dragOverIndex === idx }"
            @dragstart="onDragStart($event, idx)"
            @dragover.prevent="sortMode && onDragOver(idx)"
            @dragleave="onDragLeave"
            @drop="onDrop(idx)"
            @dragend="onDragEnd"
          >
            <td v-if="sortMode" class="col-drag">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor" class="drag-icon">
                <circle cx="4" cy="4" r="1.5"/><circle cx="10" cy="4" r="1.5"/>
                <circle cx="4" cy="10" r="1.5"/><circle cx="10" cy="10" r="1.5"/>
              </svg>
            </td>
            <td class="col-code">{{ item.stockCode }}</td>
            <td class="col-name">{{ item.stockName }}</td>
            <td class="col-market">
              <span class="item-tag sub">{{ marketLabel(item.subMarket) }}</span>
            </td>
            <td class="col-sector">
              <span class="item-tag sector" v-if="item.sector">{{ marketLabel(item.sector) }}</span>
              <span v-else>-</span>
            </td>
            <td class="col-time">{{ formatTime(item.createdAt) }}</td>
            <td class="col-action">
              <button class="btn-delete" @click="confirmDelete(item)">删除</button>
            </td>
          </tr>
          <tr v-if="!store.loading && store.list.length === 0">
            <td :colspan="sortMode ? 7 : 6" class="empty-row">暂无自选股，点击"+ 添加自选股"开始添加</td>
          </tr>
        </tbody>
      </table>
    </div>

    <Pagination
      :current="page"
      :total="store.total"
      :page-size="pageSize"
      :total-pages="store.totalPages"
      @change="handlePageChange"
      @size-change="handleSizeChange"
    />

    <AddStockDialog
      :visible="showAddDialog"
      :added-codes="addedCodes"
      @close="showAddDialog = false"
      @added="onAdded"
    />

    <ConfirmDialog
      :visible="!!deleteTarget"
      title="删除自选股"
      :message="`确定删除自选股「${deleteTarget?.stockName || ''}」(${deleteTarget?.stockCode || ''}) 吗？`"
      @confirm="handleDelete"
      @cancel="deleteTarget = null"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useWatchlistStore } from '@/stores/watchlist'
import { marketLabel } from '@/utils/market'
import Pagination from '@/components/Pagination.vue'
import AddStockDialog from '@/components/AddStockDialog.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import type { WatchlistItem } from '@/api/watchlist'

const store = useWatchlistStore()
const page = ref(0)
const pageSize = ref(20)
const keyword = ref('')
const showAddDialog = ref(false)
const deleteTarget = ref<WatchlistItem | null>(null)
const sortMode = ref(false)
const dragIndex = ref(-1)
const dragOverIndex = ref(-1)

let searchTimer: ReturnType<typeof setTimeout> | null = null

const addedCodes = computed(() => store.list.map((item: WatchlistItem) => item.stockCode))

function fetchList() {
  store.fetchList(page.value, pageSize.value, keyword.value || undefined)
}

function onSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 0
    fetchList()
  }, 300)
}

function handlePageChange(p: number) {
  page.value = p
  fetchList()
}

function handleSizeChange(s: number) {
  pageSize.value = s
  page.value = 0
  fetchList()
}

function onAdded() {
  fetchList()
}

function confirmDelete(item: WatchlistItem) {
  deleteTarget.value = item
}

async function handleDelete() {
  if (!deleteTarget.value) return
  await store.removeStock(deleteTarget.value.id)
  deleteTarget.value = null
  fetchList()
}

function toggleSort() {
  sortMode.value = !sortMode.value
}

function onDragStart(e: DragEvent, idx: number) {
  dragIndex.value = idx
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
  }
}

function onDragOver(idx: number) {
  dragOverIndex.value = idx
}

function onDragLeave() {
  dragOverIndex.value = -1
}

async function onDrop(idx: number) {
  if (dragIndex.value < 0 || dragIndex.value === idx) {
    dragOverIndex.value = -1
    return
  }
  const list = [...store.list]
  const [item] = list.splice(dragIndex.value, 1)
  list.splice(idx, 0, item)

  const orders = list.map((it, i) => ({ id: it.id, sortOrder: i + 1 }))
  try {
    await store.reorder(orders)
    fetchList()
  } catch {
    fetchList()
  }
  dragOverIndex.value = -1
}

function onDragEnd() {
  dragIndex.value = -1
  dragOverIndex.value = -1
}

function formatTime(ts: string) {
  if (!ts) return '-'
  return ts.replace('T', ' ').substring(0, 19)
}

onMounted(fetchList)
</script>

<style scoped>
.page {
  padding: 24px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.search-box input {
  width: 200px;
  padding: 6px 10px;
  background-color: var(--input-bg);
  border: 1px solid var(--input-border);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-size: 13px;
  outline: none;
}

.search-box input:focus {
  border-color: var(--input-focus-border);
}

.search-box input::placeholder {
  color: var(--text-muted);
}

.btn-sort {
  padding: 6px 14px;
  font-size: 13px;
  background-color: var(--bg-tertiary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.btn-sort:hover, .btn-sort.active {
  background-color: var(--accent-info);
  border-color: var(--accent-info);
  color: #fff;
}

.btn-add {
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  background-color: var(--btn-primary-bg);
  color: var(--btn-primary-text);
  border-radius: var(--radius);
  cursor: pointer;
}

.btn-add:hover {
  background-color: var(--btn-primary-hover);
}

.table-wrapper {
  overflow-x: auto;
}

.stock-table {
  width: 100%;
  border-collapse: collapse;
}

.stock-table th {
  padding: 10px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-align: left;
  border-bottom: 1px solid var(--border-primary);
  white-space: nowrap;
}

.stock-table td {
  padding: 10px 12px;
  font-size: 13px;
  color: var(--text-primary);
  border-bottom: 1px solid var(--border-secondary);
}

.stock-table tbody tr:hover {
  background-color: var(--bg-secondary);
}

.stock-table tr.dragging {
  opacity: 0.4;
}

.stock-table tr.drag-over {
  border-top: 2px solid var(--accent-info);
}

.col-drag {
  width: 36px;
  text-align: center;
}

.drag-icon {
  color: var(--text-muted);
  cursor: grab;
}

.col-code {
  font-family: var(--font-mono);
  font-weight: 600;
  width: 110px;
}

.col-name {
  min-width: 120px;
}

.col-market {
  width: 70px;
}

.col-sector {
  width: 100px;
}

.item-tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
  white-space: nowrap;
}

.item-tag.sub {
  color: var(--accent-info);
  background-color: rgba(88, 166, 255, 0.1);
}

.item-tag.sector {
  color: var(--accent-success);
  background-color: rgba(63, 185, 80, 0.1);
}

.col-time {
  width: 160px;
  font-size: 12px;
  color: var(--text-secondary);
}

.col-action {
  width: 70px;
  text-align: center;
}

.btn-delete {
  padding: 3px 10px;
  font-size: 12px;
  background: transparent;
  border: 1px solid var(--border-primary);
  border-radius: var(--radius);
  color: var(--text-muted);
  cursor: pointer;
  transition: all 0.15s;
}

.btn-delete:hover {
  color: var(--accent-danger);
  border-color: var(--accent-danger);
}

.empty-row {
  text-align: center;
  padding: 48px 16px;
  color: var(--text-muted);
  font-size: 14px;
}
</style>
