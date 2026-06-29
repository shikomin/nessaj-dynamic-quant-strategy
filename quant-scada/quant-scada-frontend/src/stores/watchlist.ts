import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  getWatchlistApi,
  addWatchlistApi,
  deleteWatchlistApi,
  reorderWatchlistApi,
  searchStocksApi
} from '@/api/watchlist'
import type { WatchlistItem, ReorderItem, StockSearchItem } from '@/api/watchlist'

export const useWatchlistStore = defineStore('watchlist', () => {
  const list = ref<WatchlistItem[]>([])
  const total = ref(0)
  const totalPages = ref(0)
  const loading = ref(false)

  async function fetchList(page: number, size: number, keyword?: string) {
    loading.value = true
    try {
      const res: any = await getWatchlistApi(page, size, keyword)
      list.value = res.data.content
      total.value = res.data.totalElements
      totalPages.value = res.data.totalPages
    } finally {
      loading.value = false
    }
  }

  async function addStock(stockCode: string) {
    await addWatchlistApi(stockCode)
  }

  async function removeStock(id: number) {
    await deleteWatchlistApi(id)
  }

  async function reorder(orders: ReorderItem[]) {
    await reorderWatchlistApi(orders)
  }

  async function searchStocks(keyword: string, subMarket?: string): Promise<StockSearchItem[]> {
    const res: any = await searchStocksApi(keyword, subMarket)
    return res.data || []
  }

  return { list, total, totalPages, loading, fetchList, addStock, removeStock, reorder, searchStocks }
})
