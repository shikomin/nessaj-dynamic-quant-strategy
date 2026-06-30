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
            v-for="(p, i) in store.PERIODS"
            :key="p.value"
            class="ruler-mark"
            :class="{ active: store.period === p.value, disabled: !store.isPeriodAvailable(p.value) }"
            @click="store.changePeriod(p.value)"
          >
            <div class="ruler-dot" />
            <span class="ruler-label">{{ p.label }}</span>
          </div>
        </div>
      </div>

      <div class="date-picker-wrap" v-if="activeTab === 'hist' && store.selectedCode">
        <span class="current-date" @click="showDatePicker = !showDatePicker">{{ store.currentDate || '--' }}</span>
        <div class="date-dropdown" v-if="showDatePicker">
          <div class="date-grid">
            <div
              v-for="d in dateGrid"
              :key="d.date"
              class="date-cell"
              :class="{ available: d.available, selected: d.date === store.currentDate, today: d.isToday }"
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
          :candles="store.candles"
          :cover-visible="!hasData"
          :loading="store.loading"
          :date="store.currentDate"
          :period="store.PERIODS[0].value"
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
// 数据流:
//   selectStock → loadDates() → 选最新日期 → loadKline(start,end)
//   changePeriod → loadKline (视口不重置, 引擎用现有视口渲染新蜡烛)
//   changeDate   → loadKline + 引擎 resetDay + resetViewport
// ============================================================

import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useMarketStore } from '@/stores/market'
import { marketLabel } from '@/utils/market'
import { searchStocksApi } from '@/api/watchlist'
import KLineChart from '@/components/KLineChart.vue'
import type { StockSearchItem } from '@/api/watchlist'
import type { KlineCandle } from '@/api/market'

// ---- 状态 ----
const store = useMarketStore()
const activeTab = ref('hist')                            // 'hist' | 'rt'
const keyword = ref('')                                  // 股票搜索输入
const results = ref<StockSearchItem[]>([])               // 搜索结果列表
const showDropdown = ref(false)                          // 搜索结果下拉
const showDatePicker = ref(false)                        // 日期选择器下拉
const searching = ref(false)
const searchInput = ref<HTMLInputElement | null>(null)
const crossData = ref<{ ts: number; price: number; candle: KlineCandle | null } | null>(null)

let timer: ReturnType<typeof setTimeout> | null = null   // 搜索防抖定时器

// ---- 计算属性 ----

/** 有数据且非加载中 → 显示 chart; 否则显示 cover mask */
const hasData = computed(() => store.candles.length > 0 && !store.loading)

/** 当日行情: 从当前 candles 中提取 OHLC + 总成交量 */
const todayOCHL = computed(() => {
  const cs = store.candles
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
  const dateSet = new Set(store.availableDates)

  const grid: { date: string; day: number; week: string; available: boolean; isToday: boolean }[] = []
  for (let d = 1; d <= daysInMonth; d++) {
    const dt = new Date(year, month, d)
    const ds = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    grid.push({
      date: ds, day: d, week: WEEKDAYS[dt.getDay()],
      available: dateSet.has(ds),                          // 有数据 → 绿色可点
      isToday: d === now.getDate()                         // 今天 → 蓝色边框
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

/** 选中搜索结果 → 加载该股票的日期列表 + K线 */
function select(item: StockSearchItem) {
  store.selectStock(item.code, item.name, item.subMarket || '')
  keyword.value = ''; results.value = []; showDropdown.value = false; showDatePicker.value = false
}

// ---- 日期选择 ----

/** 点击日期单元格 → 切换查询日期 */
async function onDatePick(d: { available: boolean; date: string }) {
  if (!d.available) return
  showDatePicker.value = false
  await store.changeDate(d.date)
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

onMounted(() => document.addEventListener('click', handleClickOutside))
onUnmounted(() => document.removeEventListener('click', handleClickOutside))
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
.ruler-mark.active .ruler-dot { width: 14px; height: 14px; background: var(--accent-info); box-shadow: 0 0 8px rgba(88,166,255,0.5); }
.ruler-mark.disabled { opacity: 0.3; cursor: not-allowed; pointer-events: none; }
.ruler-label { font-size: 10px; color: var(--text-muted); }
.ruler-mark.active .ruler-label { color: var(--accent-info); font-weight: 600; }

.date-picker-wrap { position: relative; margin-left: auto; }
.current-date { font-size: 12px; color: var(--accent-info); cursor: pointer; padding: 4px 10px; border-radius: var(--radius); background: rgba(88,166,255,0.1); }
.current-date:hover { background: rgba(88,166,255,0.2); }
.date-dropdown { position: absolute; top: calc(100% + 6px); right: 0; z-index: 60; background: var(--bg-secondary); border: 1px solid var(--border-primary); border-radius: var(--radius); padding: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); }
.date-grid { display: grid; grid-template-columns: repeat(7, 48px); gap: 2px; }
.date-cell { display: flex; flex-direction: column; align-items: center; padding: 6px 4px; border-radius: var(--radius); cursor: default; }
.date-cell.available { cursor: pointer; color: var(--accent-success); }
.date-cell.available:hover { background: rgba(63,185,80,0.1); }
.date-cell.selected { background: var(--accent-info); color: #fff; }
.date-cell.selected.available { background: var(--accent-info); color: #fff; }
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
.dropdown { position: absolute; top: calc(100% + 4px); left: 0; right: 0; max-height: 260px; overflow-y: auto; background: var(--bg-secondary); border: 1px solid var(--border-primary); border-radius: var(--radius); box-shadow: 0 8px 24px rgba(0,0,0,0.4); z-index: 50; }
.dropdown-item { display: flex; align-items: center; gap: 6px; padding: 6px 10px; cursor: pointer; font-size: 12px; }
.dropdown-item:hover { background: var(--bg-overlay); }
.dropdown-item.muted { color: var(--text-muted); cursor: default; }
.dropdown-item.muted:hover { background: transparent; }
.dd-code { font-family: var(--font-mono); font-weight: 600; color: var(--text-primary); min-width: 60px; }
.dd-name { color: var(--text-primary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dd-tag { font-size: 10px; color: var(--accent-info); }

.cover-mask.rt-placeholder { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--text-muted); font-size: 15px; }
</style>
