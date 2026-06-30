// ============================================================
// 行情 Store — 只保存跨页面共享的选中股票信息
// ============================================================
// K 线数据、加载状态、周期、日期等页面级状态由 MarketView.vue 本地管理
// ============================================================

import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 可用采样周期 (供 MarketView 和 KLineChart 使用) */
export const PERIODS = [
  { value: '5s', label: '5秒' },
  { value: '15s', label: '15秒' },
  { value: '30s', label: '30秒' },
  { value: '1m', label: '1分' },
  { value: '5m', label: '5分' },
  { value: '15m', label: '15分' },
] as const

export const useMarketStore = defineStore('market', () => {
  /** 当前选中股票代码 */
  const selectedCode = ref('')
  /** 当前选中股票名称 */
  const selectedName = ref('')
  /** 当前选中股票市场 (sh/sz/bj) */
  const selectedMarket = ref('')

  function clear() {
    selectedCode.value = ''
    selectedName.value = ''
    selectedMarket.value = ''
  }

  return { selectedCode, selectedName, selectedMarket, clear }
})
