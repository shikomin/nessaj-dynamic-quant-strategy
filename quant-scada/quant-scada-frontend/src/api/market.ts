import http from './request'

export interface KlineCandle {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
}

export function fetchKlineApi(code: string, market: string, period = '1m', start?: string, end?: string, limit = 240) {
  return http.get('/api/market/kline', { params: { code, market, period, start, end, limit } })
}

export function fetchKlineDatesApi(code: string, market: string) {
  return http.get('/api/market/kline/dates', { params: { code, market } })
}
