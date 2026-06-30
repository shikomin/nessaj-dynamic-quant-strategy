import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { fetchKlineApi, fetchKlineDatesApi } from '@/api/market'
import type { KlineCandle } from '@/api/market'

export const PERIODS = [
  { value: '1m', label: '1分' },
  { value: '5m', label: '5分' },
  { value: '15m', label: '15分' },
] as const

export const useMarketStore = defineStore('market', () => {
  const selectedCode = ref('')
  const selectedName = ref('')
  const selectedMarket = ref('')
  const candles = ref<KlineCandle[]>([])
  const loading = ref(false)
  const error = ref('')
  const period = ref('1m')
  const currentDate = ref('')
  const availableDates = ref<string[]>([])

  function isPeriodAvailable(p: string) {
    if (!selectedCode.value) return p === '1m'
    const ms = candles.value.length >= 2
      ? new Date(candles.value[candles.value.length - 1].ts).getTime() -
        new Date(candles.value[0].ts).getTime()
      : 0
    if (ms <= 0) return p === '1m'
    const periodMs = parseInt(p) * 60 * 1000
    return ms >= periodMs * 10
  }

  async function selectStock(code: string, name: string, market: string) {
    selectedCode.value = code
    selectedName.value = name
    selectedMarket.value = market
    period.value = '1m'
    currentDate.value = ''
    await loadDates()
    if (availableDates.value.length > 0) {
      currentDate.value = availableDates.value[0]
    }
    await loadKline()
  }

  async function loadDates() {
    if (!selectedCode.value) return
    try {
      const res: any = await fetchKlineDatesApi(selectedCode.value)
      if (res.code === 200) availableDates.value = res.data || []
    } catch { availableDates.value = [] }
  }

  async function changeDate(date: string) {
    if (date === currentDate.value) return
    currentDate.value = date
    await loadKline()
  }

  async function loadKline() {
    if (!selectedCode.value) return
    loading.value = true
    error.value = ''
    try {
      const start = currentDate.value ? `${currentDate.value} 00:00:00` : undefined
      const end = currentDate.value ? `${currentDate.value} 23:59:59` : undefined
      const res: any = await fetchKlineApi(selectedCode.value, period.value, start, end)
      if (res.code === 200) {
        candles.value = res.data
      } else {
        candles.value = []
        error.value = res.msg || '加载失败'
      }
    } catch (e: any) {
      candles.value = []
      error.value = e.message || '网络错误'
    } finally {
      loading.value = false
    }
  }

  async function changePeriod(p: string) {
    if (p === period.value || !isPeriodAvailable(p)) return
    period.value = p
    await loadKline()
  }

  function clear() {
    selectedCode.value = ''
    selectedName.value = ''
    selectedMarket.value = ''
    candles.value = []
    error.value = ''
    period.value = '1m'
    currentDate.value = ''
    availableDates.value = []
  }

  return { selectedCode, selectedName, selectedMarket, candles, loading, error, period, currentDate, availableDates, PERIODS, selectStock, loadKline, changePeriod, changeDate, isPeriodAvailable, clear }
})
