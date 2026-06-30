<template>
  <div ref="wrapRef" class="chart-wrap">
    <canvas ref="canvasRef"
      @mousedown="onMouseDown" @mousemove="onMouseMove"
      @mouseup="onMouseUp" @wheel="onWheel" @click="onClick"
      @mouseleave="onMouseLeave" />
    <div class="cover-mask" v-if="coverVisible">
      <span v-if="loading" class="loading-bars">
        <span class="bar" v-for="i in 5" :key="i" :style="{ animationDelay: i * 0.12 + 's' }" />
      </span>
      <span v-else class="no-data">暂无数据</span>
    </div>
  </div>
</template>

<script setup lang="ts">
// ============================================================
// K 线图表组件 — Canvas 渲染的 K 线图, 支持缩放/拖拽/十字光标
// ============================================================
// Props:
//   candles      原始 K 线数据 (从 API 返回)
//   coverVisible 是否显示遮罩 (loading / 无数据)
//   loading      是否为加载中状态
//   date         当前查询日期 (用于构建交易时段)
//   period       最小缩放周期 (如 "5s"/"1m"), 决定缩放下限
//
// 初始化时序:
//   onMounted 创建 engine → 立即应用 date + period + viewport
//   之后 date/period watcher (均 immediate) 也可触发初始化
// ============================================================

import { ref, watch, onMounted, onUnmounted } from 'vue'
import { KlineEngine, DARK_CHART_COLORS, LIGHT_CHART_COLORS } from './KlineEngine'
import { useSettingsStore } from '@/stores/settings'
import type { Candle } from './KlineEngine'
import type { KlineCandle } from '@/api/market'

const props = defineProps<{
  candles: KlineCandle[]
  coverVisible: boolean
  loading: boolean
  date: string
  period: string
}>()

const emit = defineEmits<{
  crosshair: [data: { ts: number; price: number; candle: KlineCandle | null }]
}>()

const wrapRef = ref<HTMLDivElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)

const settingsStore = useSettingsStore()

let engine: KlineEngine | null = null
let candlesCache: Candle[] = []
let rafId = 0
let isDragging = false
let lastX = 0

/** 将当前主题配色方案同步到 engine */
function syncTheme() {
  if (!engine) return
  engine.setColors(settingsStore.theme === 'light' ? LIGHT_CHART_COLORS : DARK_CHART_COLORS)
}

/** API 格式 → 引擎内部 Candle 格式 */
function toEngine(c: KlineCandle): Candle {
  return { ts: new Date(c.ts).getTime(), open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume }
}

function toEngineList(raw: KlineCandle[]) { return raw.map(toEngine) }

/** 渲染循环 */
function frame() {
  if (!engine) return
  engine.render(candlesCache)
  rafId = requestAnimationFrame(frame)
}

/** 将十字光标数据 emit 给父组件 */
function emitCrosshair() {
  if (!engine || props.coverVisible) return
  const data = engine.getCrosshairData(candlesCache)
  if (data) {
    emit('crosshair', {
      ts: data.ts,
      price: data.price,
      candle: data.candle ? {
        ts: new Date(data.candle.ts).toISOString(),
        open: data.candle.open, high: data.candle.high,
        low: data.candle.low, close: data.candle.close,
        volume: data.candle.volume, amount: 0
      } : null
    })
  }
}

// ---- 鼠标事件 ----

function onMouseDown(e: MouseEvent) { isDragging = true; lastX = e.clientX }
function onMouseUp() { isDragging = false }

function onMouseMove(e: MouseEvent) {
  if (!engine) return
  const rect = canvasRef.value!.getBoundingClientRect()
  engine.mouseX = e.clientX - rect.left
  engine.mouseY = e.clientY - rect.top
  engine.showCrosshair = !isDragging
  if (isDragging) { engine.pan(e.clientX - lastX); lastX = e.clientX }
  emitCrosshair()
}

function onMouseLeave() {
  if (!engine) return
  engine.showCrosshair = false
  isDragging = false
}

function onWheel(e: WheelEvent) {
  if (!engine) return
  e.preventDefault()
  const rect = canvasRef.value!.getBoundingClientRect()
  const s = e.deltaY > 0 ? 0.85 : 1.15
  if (e.shiftKey) engine.zoomPriceAt(e.clientY - rect.top, s)
  else engine.zoomAt(e.clientX - rect.left, s)
}

function onClick(e: MouseEvent) {
  if (!engine || isDragging) return
  const rect = canvasRef.value!.getBoundingClientRect()
  engine.handleClick(e.clientX - rect.left, e.clientY - rect.top, candlesCache)
}

function handleResize() { engine?.resize() }

// ---- Watchers ----

/** 蜡烛数据变更 → 仅更新缓存, 不重建 engine / 不重置视口 */
watch(() => props.candles, (raw) => {
  candlesCache = toEngineList(raw || [])
}, { deep: true, immediate: true })

/** 日期变更 → 重建交易时段 + 重置视口 */
watch(() => props.date, (d) => {
  if (!d || !engine) return
  engine.setDay(d)
  engine.resetPriceRange()
  engine.resetViewport(candlesCache)
}, { immediate: true })

/** 缩放周期变更 → 更新缩放下限 (保留当前视口) */
watch(() => props.period, (p) => {
  if (engine) engine.setMinPeriod(p)
}, { immediate: true })

/** 主题切换 → 同步配色方案到 engine (无需重绘, 下一帧自动生效) */
watch(() => settingsStore.theme, () => { syncTheme() })

// ---- 生命周期 ----

/**
 * 挂载时创建 engine 并立即应用当前 props 和主题。
 * 这是 engine 创建的唯一入口, candles/date/period watcher 仅处理后续变更。
 */
onMounted(() => {
  window.addEventListener('resize', handleResize)
  if (canvasRef.value) {
    engine = new KlineEngine(canvasRef.value)
    syncTheme()
    engine.setMinPeriod(props.period)
    if (props.date) {
      engine.setDay(props.date)
      engine.resetViewport(candlesCache)
      engine.resetPriceRange()
    }
  }
  if (!rafId) frame()
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  cancelAnimationFrame(rafId)
  engine = null
})
</script>

<style scoped>
.chart-wrap {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
  cursor: crosshair;
  background-color: var(--bg-primary);
}

.chart-wrap canvas {
  position: absolute;
  top: 0;
  left: 0;
}

.cover-mask {
  position: absolute;
  inset: 0;
  background: var(--cover-mask-bg);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  z-index: 10;
}

.no-data {
  font-size: 15px;
  color: var(--text-muted);
}

.loading-bars {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 48px;
}

.loading-bars .bar {
  width: 8px;
  border-radius: 4px;
  background: linear-gradient(180deg, var(--accent-info) 0%, var(--accent-info-faded) 100%);
  animation: loading-wave 0.8s ease-in-out infinite;
}

@keyframes loading-wave {
  0%, 100% { height: 12px; opacity: 0.3; }
  50% { height: 40px; opacity: 1; }
}
</style>
