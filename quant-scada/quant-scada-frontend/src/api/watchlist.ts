import http from './request'

export interface WatchlistItem {
  id: number
  userId: number
  stockCode: string
  stockName: string
  market: string
  subMarket: string
  sector: string
  sortOrder: number
  createdAt: string
}

export interface WatchlistPage {
  content: WatchlistItem[]
  totalElements: number
  totalPages: number
  number: number
  size: number
}

export interface StockSearchItem {
  code: string
  name: string
  market: string
  subMarket: string
  sector: string
}

export interface ReorderItem {
  id: number
  sortOrder: number
}

export function getWatchlistApi(page: number, size: number, keyword?: string) {
  return http.get('/api/watchlist', { params: { page, size, keyword } })
}

export function addWatchlistApi(stockCode: string) {
  return http.post('/api/watchlist', { stockCode })
}

export function deleteWatchlistApi(id: number) {
  return http.delete(`/api/watchlist/${id}`)
}

export function reorderWatchlistApi(orders: ReorderItem[]) {
  return http.put('/api/watchlist/reorder', { orders })
}

export function searchStocksApi(keyword: string, subMarket?: string) {
  return http.get('/api/stock/search', { params: { keyword, subMarket } })
}
