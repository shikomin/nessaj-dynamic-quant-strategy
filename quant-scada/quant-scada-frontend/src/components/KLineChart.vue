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
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { KlineEngine } from './KlineEngine'
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

let engine: KlineEngine | null = null
let candlesCache: Candle[] = []
let rafId = 0
let isDragging = false
let lastX = 0

function toEngine(c: KlineCandle): Candle {
  return { ts: new Date(c.ts).getTime(), open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume }
}

function toEngineList(raw: KlineCandle[]) { return raw.map(toEngine) }

function frame() {
  if (!engine) return
  engine.render(candlesCache)
  rafId = requestAnimationFrame(frame)
}

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

watch(() => props.candles, async (raw) => {
  candlesCache = toEngineList(raw || [])
  await nextTick()
  if (!engine && canvasRef.value) engine = new KlineEngine(canvasRef.value)
  if (!rafId) frame()
}, { deep: true, immediate: true })

watch(() => props.date, (d) => {
  if (!d || !engine) return
  engine.setDay(d)
  engine.resetPriceRange()
  engine.resetViewport(candlesCache)
})

watch(() => props.period, (p) => {
  if (engine) engine.setMinPeriod(p)
})

onMounted(() => {
  window.addEventListener('resize', handleResize)
  if (canvasRef.value && !engine) engine = new KlineEngine(canvasRef.value)
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
  background-color: #0d1117;
}

.chart-wrap canvas {
  position: absolute;
  top: 0;
  left: 0;
}

.cover-mask {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.25);
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
  gap: 4px;
  height: 40px;
}

.loading-bars .bar {
  width: 6px;
  height: 10px;
  border-radius: 3px;
  background: var(--accent-info);
  animation: loading-wave 0.8s ease-in-out infinite;
}

@keyframes loading-wave {
  0%, 100% { height: 10px; opacity: 0.4; }
  50% { height: 30px; opacity: 1; }
}
</style>
