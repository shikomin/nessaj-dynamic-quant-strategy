<template>
  <div class="page">
    <div class="top-bar">
      <div class="tabs">
        <button class="tab" :class="{ active: activeTab === 'hist' }" @click="activeTab = 'hist'">历史行情</button>
        <button class="tab" :class="{ active: activeTab === 'rt' }" @click="activeTab = 'rt'">实时行情</button>
      </div>

      <div class="period-ruler" v-if="activeTab === 'hist' && store.selectedCode">
        <div class="ruler-track">
          <div class="ruler-line" />
          <div
            v-for="(p, i) in PERIODS"
            :key="p.value"
            class="ruler-mark"
            :class="{ active: period === p.value, disabled: !isPeriodAvailable(p.value) }"
            @click="changePeriod(p.value)"
          >
            <div class="ruler-dot" />
            <span class="ruler-label">{{ p.label }}</span>
          </div>
        </div>
      </div>

      <div class="date-picker-wrap" v-if="activeTab === 'hist' && store.selectedCode">
        <span class="current-date" @click="showDatePicker = !showDatePicker">{{ currentDate || '--' }}</span>
        <div class="date-dropdown" v-if="showDatePicker">
          <div class="date-grid">
            <div
              v-for="d in dateGrid"
              :key="d.date"
              class="date-cell"
              :class="{ available: d.available, selected: d.date === currentDate, today: d.isToday }"
              @click="onDatePick(d)"
            >
              <span class="date-num">{{ d.day }}</span>
              <span class="date-week">{{ d.week }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="page-body" v-if="activeTab === 'hist'">
      <div class="chart-area">
        <KLineChart
          :candles="candles"
          :cover-visible="!hasData"
          :loading="loading"
          :date="currentDate"
          :period="PERIODS[0].value"
          @crosshair="onCrosshair"
        />
      </div>
      <div class="side-panel">
        <div class="panel-section">
          <div class="stock-picker">
            <input ref="searchInput" v-model="keyword" type="text" placeholder="搜索代码或名称..."
              @input="onSearch" @focus="showDropdown = true" />
            <div class="dropdown" v-if="showDropdown && (keyword || results.length > 0)">
              <div class="dropdown-item muted" v-if="keyword && results.length === 0 && !searching">无匹配结果</div>
              <div v-for="item in results" :key="item.code" class="dropdown-item" @click="select(item)">
                <span class="dd-code">{{ item.code }}</span><span class="dd-name">{{ item.name }}</span>
                <span class="dd-tag">{{ marketLabel(item.subMarket) }}</span>
              </div>
            </div>
          </div>
        </div>
        <div class="panel-section" v-if="store.selectedCode">
          <div class="section-title">股票信息</div>
          <div class="info-grid">
            <div class="info-row"><span class="label">代码</span><span>{{ store.selectedCode }}</span></div>
            <div class="info-row"><span class="label">名称</span><span>{{ store.selectedName }}</span></div>
            <div class="info-row" v-if="store.selectedMarket"><span class="label">市场</span><span>{{ marketLabel(store.selectedMarket) }}</span></div>
          </div>
        </div>
        <div class="panel-section" v-if="todayOCHL">
          <div class="section-title">今日行情</div>
          <div class="info-grid">
            <div class="info-row"><span class="label">开盘</span><span class="val">{{ todayOCHL.open.toFixed(2) }}</span></div>
            <div class="info-row"><span class="label">最高</span><span class="val up">{{ todayOCHL.high.toFixed(2) }}</span></div>
            <div class="info-row"><span class="label">最低</span><span class="val down">{{ todayOCHL.low.toFixed(2) }}</span></div>
            <div class="info-row"><span class="label">昨收</span><span>{{ todayOCHL.preClose.toFixed(2) }}</span></div>
            <div class="info-row"><span class="label">成交量</span><span>{{ fmtVol(todayOCHL.volume) }}</span></div>
          </div>
        </div>
        <div class="panel-section" v-if="crossData">
          <div class="section-title">光标位置</div>
          <div class="info-grid">
            <div class="info-row"><span class="label">时间</span><span>{{ fmtTs(crossData.ts) }}</span></div>
            <div class="info-row"><span class="label">价格</span><span>{{ crossData.price.toFixed(2) }}</span></div>
            <template v-if="crossData.candle">
              <div class="info-row"><span class="label">开盘</span><span>{{ crossData.candle.open.toFixed(2) }}</span></div>
              <div class="info-row"><span class="label">最高</span><span class="val up">{{ crossData.candle.high.toFixed(2) }}</span></div>
              <div class="info-row"><span class="label">最低</span><span class="val down">{{ crossData.candle.low.toFixed(2) }}</span></div>
              <div class="info-row"><span class="label">收盘</span><span>{{ crossData.candle.close.toFixed(2) }}</span></div>
              <div class="info-row"><span class="label">成交量</span><span>{{ fmtVol(crossData.candle.volume) }}</span></div>
            </template>
          </div>
        </div>
      </div>
    </div>
    <div class="cover-mask rt-placeholder" v-else>实时行情功能开发中...</div>
  </div>
</template>

<script setup lang="ts">
// ============================================================
// 行情页 — 历史行情 / 实时行情 双页签
// ============================================================
// 布局: [页签] [周期标尺] [日期选择器] 同行
//       [左侧: Canvas K线] [右侧: 搜索/信息/行情/光标面板]
//
// 状态归属:
//   全局 store: selectedCode, selectedName, selectedMarket (跨页面共享)
//   本地 ref:   candles, loading, error, period, currentDate, availableDates
//
// 数据流:
//   selectStock → loadDates() → 选最新日期 → loadKline(start,end)
//   changePeriod → loadKline (视口不重置, 引擎用现有视口渲染新蜡烛)
//   changeDate   → loadKline + 引擎通过 watcher 自动 resetDay + resetViewport
// ============================================================

import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useMarketStore } from '@/stores/market'
import { PERIODS } from '@/stores/market'
import { marketLabel } from '@/utils/market'
import { fetchKlineApi, fetchKlineDatesApi } from '@/api/market'
import { searchStocksApi } from '@/api/watchlist'
import KLineChart from '@/components/KLineChart.vue'
import type { KlineCandle } from '@/api/market'
import type { StockSearchItem } from '@/api/watchlist'

// ---- 全局 store (仅选中股票信息) ----
const store = useMarketStore()

// ---- 本地页面状态 ----
const activeTab = ref<'hist' | 'rt'>('hist')
const keyword = ref('')
const results = ref<StockSearchItem[]>([])
const showDropdown = ref(false)
const showDatePicker = ref(false)
const searching = ref(false)
const searchInput = ref<HTMLInputElement | null>(null)
const crossData = ref<{ ts: number; price: number; candle: KlineCandle | null } | null>(null)

/** 当前查询周期 */
const period = ref('1m')
/** 当前查询日期 */
const currentDate = ref('')
/** 可选日期列表 */
const availableDates = ref<string[]>([])
/** 当前 K 线数据 */
const candles = ref<KlineCandle[]>([])
/** 加载状态 */
const loading = ref(false)
/** 错误信息 */
const error = ref('')

let timer: ReturnType<typeof setTimeout> | null = null

// ---- 计算属性 ----

/** 有数据且非加载中 → 显示 chart; 否则显示 cover mask */
const hasData = computed(() => candles.value.length > 0 && !loading.value)

/** 当日行情: 从当前 candles 中提取 OHLC + 总成交量 */
const todayOCHL = computed(() => {
  const cs = candles.value
  if (cs.length === 0) return null
  let high = -Infinity, low = Infinity, vol = 0
  for (const c of cs) { if (c.high > high) high = c.high; if (c.low < low) low = c.low; vol += c.volume }
  return { open: cs[0].open, high, low, preClose: cs[0].open, volume: vol }
})

// ---- 日期选择器网格 ----
const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

const dateGrid = computed(() => {
  const now = new Date()
  const year = now.getFullYear()
  const month = now.getMonth()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const dateSet = new Set(availableDates.value)

  const grid: { date: string; day: number; week: string; available: boolean; isToday: boolean }[] = []
  for (let d = 1; d <= daysInMonth; d++) {
    const dt = new Date(year, month, d)
    const ds = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    grid.push({
      date: ds, day: d, week: WEEKDAYS[dt.getDay()],
      available: dateSet.has(ds),
      isToday: d === now.getDate()
    })
  }
  return grid
})

// ---- 格式化 ----
function fmtVol(v: number) {
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '万'
  return String(v)
}

function fmtTs(ts: number) {
  const d = new Date(ts)
  return `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`
}

// ---- 周期可用性 ----

/** 判断指定周期是否有足够数据 (至少 10 根蜡烛) */
function isPeriodAvailable(p: string): boolean {
  if (!store.selectedCode) return p === '1m'
  const ms = candles.value.length >= 2
    ? new Date(candles.value[candles.value.length - 1].ts).getTime() -
      new Date(candles.value[0].ts).getTime()
    : 0
  if (ms <= 0) return p === '1m'
  const periodMs = parseInt(p) * (p.endsWith('s') ? 1000 : 60_000)
  return ms >= periodMs * 10
}

// ---- 数据加载 ----

/** 加载可选日期列表 */
async function loadDates() {
  if (!store.selectedCode) return
  try {
    const res: any = await fetchKlineDatesApi(store.selectedCode)
    if (res.code === 200) availableDates.value = res.data || []
  } catch { availableDates.value = [] }
}

/** 加载 K 线数据 */
async function loadKline() {
  if (!store.selectedCode) return
  loading.value = true
  error.value = ''
  try {
    const start = currentDate.value ? `${currentDate.value} 00:00:00` : undefined
    const end = currentDate.value ? `${currentDate.value} 23:59:59` : undefined
    const res: any = await fetchKlineApi(store.selectedCode, period.value, start, end)
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

// ---- 交互 ----

/** 选中搜索结果 → 加载该股票的日期列表 + K线 */
async function selectStock(item: StockSearchItem) {
  store.selectedCode = item.code
  store.selectedName = item.name
  store.selectedMarket = item.subMarket || ''
  period.value = '1m'
  currentDate.value = ''
  keyword.value = ''; results.value = []; showDropdown.value = false; showDatePicker.value = false
  await loadDates()
  if (availableDates.value.length > 0) {
    currentDate.value = availableDates.value[0]
  }
  await loadKline()
}

/** 切换采样周期 */
async function changePeriod(p: string) {
  if (p === period.value || !isPeriodAvailable(p)) return
  period.value = p
  await loadKline()
}

/** 切换查询日期 */
async function changeDate(date: string) {
  if (date === currentDate.value) return
  currentDate.value = date
  await loadKline()
}

// ---- 搜索 ----

/** 300ms 防抖搜索股票库 */
function onSearch() {
  if (timer) clearTimeout(timer)
  if (!keyword.value.trim()) { results.value = []; return }
  timer = setTimeout(async () => {
    searching.value = true
    try { const res: any = await searchStocksApi(keyword.value.trim()); results.value = res.data || [] }
    finally { searching.value = false }
  }, 300)
}

/** 从搜索结果中选择 (兼容 StockSearchItem) */
function select(item: StockSearchItem) { selectStock(item) }

// ---- 日期选择 ----

/** 点击日期单元格 → 切换查询日期 */
async function onDatePick(d: { available: boolean; date: string }) {
  if (!d.available) return
  showDatePicker.value = false
  await changeDate(d.date)
}

// ---- 交互 ----

/** 点击外部区域关闭下拉 */
function handleClickOutside(e: MouseEvent) {
  const t = e.target as HTMLElement
  if (!t.closest('.stock-picker')) showDropdown.value = false
  if (!t.closest('.date-picker-wrap')) showDatePicker.value = false
}

/** 接收 KLineChart emit 的十字光标数据 */
function onCrosshair(d: { ts: number; price: number; candle: KlineCandle | null }) { crossData.value = d }

/**
 * 挂载时恢复: 如果 store 中已有选中股票, 自动重新加载数据。
 * 确保页面切换后 K 线图数据不丢失。
 */
onMounted(async () => {
  document.addEventListener('click', handleClickOutside)
  if (store.selectedCode) {
    period.value = '1m'
    await loadDates()
    if (availableDates.value.length > 0) {
      currentDate.value = availableDates.value[0]
    }
    await loadKline()
  }
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; width: 100%;height: 100%; min-height: 0; padding: 16px 20px 0; overflow: hidden; }

.top-bar { display: flex; align-items: center; flex-shrink: 0; margin-bottom: 10px; border-bottom: 1px solid var(--border-primary); padding-bottom: 6px; }
.tabs { display: flex; margin-right: auto; }
.tab { padding: 6px 16px; font-size: 14px; font-weight: 600; background: transparent; border: none; color: var(--text-secondary); cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.15s; margin-bottom: -7px; }
.tab:hover { color: var(--text-primary); }
.tab.active { color: var(--accent-info); border-bottom-color: var(--accent-info); }

.period-ruler { display: flex; align-items: center; margin: 0 16px; }
.ruler-track { position: relative; display: flex; align-items: center; gap: 0; }
.ruler-line { position: absolute; left: 12px; right: 12px; top: 8px; height: 1px; background: var(--border-primary); z-index: 0; pointer-events: none; }
.ruler-mark { position: relative; z-index: 1; display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 0 14px; cursor: pointer; }
.ruler-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--border-primary); transition: all 0.2s; }
.ruler-mark.active .ruler-dot { width: 14px; height: 14px; background: var(--accent-info); box-shadow: var(--accent-info-glow); }
.ruler-mark.disabled { opacity: 0.3; cursor: not-allowed; pointer-events: none; }
.ruler-label { font-size: 10px; color: var(--text-muted); }
.ruler-mark.active .ruler-label { color: var(--accent-info); font-weight: 600; }

.date-picker-wrap { position: relative; margin-left: auto; }
.current-date { font-size: 12px; color: var(--accent-info); cursor: pointer; padding: 4px 10px; border-radius: var(--radius); background: var(--accent-info-subtle); }
.current-date:hover { background: var(--accent-info-muted); }
.date-dropdown { position: absolute; top: calc(100% + 6px); right: 0; z-index: 60; background: var(--bg-secondary); border: 1px solid var(--border-primary); border-radius: var(--radius); padding: 8px; box-shadow: var(--shadow-dropdown-heavy); }
.date-grid { display: grid; grid-template-columns: repeat(7, 48px); gap: 2px; }
.date-cell { display: flex; flex-direction: column; align-items: center; padding: 6px 4px; border-radius: var(--radius); cursor: default; }
.date-cell.available { cursor: pointer; color: var(--accent-success); }
.date-cell.available:hover { background: var(--accent-success-subtle); }
.date-cell.selected { background: var(--accent-info); color: var(--text-on-accent); }
.date-cell.selected.available { background: var(--accent-info); color: var(--text-on-accent); }
.date-cell:not(.available) { color: var(--text-muted); opacity: 0.4; }
.date-num { font-size: 14px; font-weight: 600; }
.date-week { font-size: 10px; }
.date-cell.today { border: 1px solid var(--accent-info); }

.page-body { flex: 1; display: flex; gap: 12px; min-height: 0; padding-bottom: 12px; overflow: hidden; }
.chart-area { flex: 1; min-width: 0; min-height: 0; border: 1px solid var(--border-primary); border-radius: var(--radius); overflow: hidden; }

.side-panel { width: 220px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; overflow-y: auto; }
.panel-section { background: var(--bg-secondary); border: 1px solid var(--border-primary); border-radius: var(--radius); padding: 12px; }
.section-title { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
.info-grid { display: flex; flex-direction: column; gap: 4px; }
.info-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-primary); }
.info-row .label { color: var(--text-muted); }
.val.up { color: var(--accent-danger); }
.val.down { color: var(--accent-success); }

.stock-picker { position: relative; }
.stock-picker input { width: 100%; padding: 6px 10px; font-size: 12px; background: var(--input-bg); border: 1px solid var(--input-border); border-radius: var(--radius); color: var(--text-primary); outline: none; }
.stock-picker input:focus { border-color: var(--input-focus-border); }
.stock-picker input::placeholder { color: var(--text-muted); }
.dropdown { position: absolute; top: calc(100% + 4px); left: 0; right: 0; max-height: 260px; overflow-y: auto; background: var(--bg-secondary); border: 1px solid var(--border-primary); border-radius: var(--radius); box-shadow: var(--shadow-dropdown); z-index: 50; }
.dropdown-item { display: flex; align-items: center; gap: 6px; padding: 6px 10px; cursor: pointer; font-size: 12px; }
.dropdown-item:hover { background: var(--bg-overlay); }
.dropdown-item.muted { color: var(--text-muted); cursor: default; }
.dropdown-item.muted:hover { background: transparent; }
.dd-code { font-family: var(--font-mono); font-weight: 600; color: var(--text-primary); min-width: 60px; }
.dd-name { color: var(--text-primary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dd-tag { font-size: 10px; color: var(--accent-info); }

.cover-mask.rt-placeholder { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--text-muted); font-size: 15px; }
</style>
