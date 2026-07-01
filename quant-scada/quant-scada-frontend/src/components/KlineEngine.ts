// ============================================================
// K 线 Canvas 渲染引擎
// ============================================================
// 坐标系:
//   tsToX:  时间戳(ms) → 画布像素 x (支持视口外外推)
//   xToTs:  像素 x → 时间戳(ms) [十字光标反向定位]
//   yFromPrice: 价格 → 画布像素 y
//
// 分段模式 (segments 非空时):
//   交易时段之间 gap 不占像素宽度, 直接拼接。
//   例如 A 股: 9:30-11:30 → (gap 塌陷) → 13:00-15:00
//   超出视口的蜡烛会线性外推到画布之外, 不会堆叠在边界。
//
// 绘制层级 (从底到顶):
//   1. 网格 + 边框
//   2. 成交量柱
//   3. 蜡烛图
//   4. 时间轴 + 价格轴
//   5. 十字光标
//   6. 蜡烛点击弹框
// ============================================================

// ---- 数据结构 ----

/** 单根 K 线蜡烛 */
export interface Candle {
  ts: number     // 时间戳(ms)
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/** 十字光标悬停数据, 传给 Vue 右侧面板展示 */
export interface CrosshairData {
  ts: number
  price: number
  candle: Candle | null
}

// ---- 配色 ----
// 中国股市习惯: 涨红跌绿

/** K 线图表配色方案接口 */
export interface ChartColors {
  bg: string
  grid: string
  text: string
  textBright: string
  textWhite: string
  up: string
  down: string
  crosshair: string
  crosshairLabel: string
  volUp: string
  volDown: string
  axisLine: string
}

/** 深色主题 (默认) */
export const DARK_CHART_COLORS: ChartColors = {
  bg: "#0d1117",
  grid: "rgba(255,255,255,0.04)",
  text: "#8b949e",
  textBright: "#c9d1d9",
  textWhite: "#ffffff",
  up: "#ef5350",
  down: "#26a69a",
  crosshair: "rgba(255,255,255,0.28)",
  crosshairLabel: "rgba(22,27,33,0.94)",
  volUp: "rgba(239,83,80,0.3)",
  volDown: "rgba(38,166,154,0.3)",
  axisLine: "#30363d",
}

/** 亮色主题 */
export const LIGHT_CHART_COLORS: ChartColors = {
  bg: "#ffffff",
  grid: "rgba(0,0,0,0.04)",
  text: "#656d76",
  textBright: "#1f2328",
  textWhite: "#ffffff",
  up: "#cf222e",
  down: "#1a7f37",
  crosshair: "rgba(0,0,0,0.15)",
  crosshairLabel: "rgba(234,238,242,0.94)",
  volUp: "rgba(207,34,46,0.2)",
  volDown: "rgba(26,127,55,0.2)",
  axisLine: "#d0d7de",
}

// ---- 布局常量 ----
const PADDING = { top: 12, right: 22, bottom: 42, left: 50 }
const VOL_RATIO = 0.22       // 成交量区占总高 22%
const CANDLE_BODY_W = 6      // 蜡烛实体宽度(px)

// ---- 引擎类 ----

export class KlineEngine {
  // 画布 & DPR
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private dpr: number          // devicePixelRatio, 用于高清渲染
  private w = 0                // CSS 像素宽/高
  private h = 0
  private chartW = 0           // 图表可用宽度 (减左右 padding)
  private chartH = 0           // 蜡烛区高度
  private volH = 0             // 成交量区高度

  // ---- 视口(绝对时间戳, ms) ----
  viewStartMs = 0
  viewEndMs = 0

  // ---- 价格轴(从蜡烛数据自动计算) ----
  private lastMinPrice = 0
  private lastMaxPrice = 1

  // ---- 价格轴手动缩放/平移覆盖 ----
  private priceMinOverride = 0
  private priceMaxOverride = 0

  // ---- 交互状态 ----
  mouseX = 0
  mouseY = 0
  showCrosshair = false
  selectedCandle: Candle | null = null

  // ---- 分段时间轴 ----
  // setDay() 创建固定两段 (上午/下午); 为空时降级为线性映射
  private segments: { startMs: number; endMs: number; startH: number; startM: number; endH: number; endM: number }[] = []
  private totalSegmentMs = 0       // 全部段的总活跃时长(ms)

  // ---- 缩放控制 ----
  // 最小视口范围 = periodMs × 16, 由外部 setMinPeriod() 设置
  private periodMs = 60_000

  // ---- 配色 (可动态切换) ----
  private colors: ChartColors = DARK_CHART_COLORS

  // ==========================================================
  // 公开 API
  // ==========================================================

  /**
   * 动态切换图表配色方案 (用于主题切换)。
   * @param c 配色方案对象, 可用 DARK_CHART_COLORS / LIGHT_CHART_COLORS
   */
  setColors(c: ChartColors) {
    this.colors = c
  }

  /**
   * 设置缩放下限所使用的"最小采样周期"
   * 缩放下限 = 该周期 × 16
   * @param p 如 "5s" / "1m" / "5m"
   */
  setMinPeriod(p: string) {
    const num = parseInt(p)
    if (p.endsWith('s')) this.periodMs = num * 1000
    else this.periodMs = num * 60 * 1000
  }

  /**
   * 用指定日期构建两段交易时段 (上午 9:30-11:30, 下午 13:00-15:00)
   * @param dateStr 如 "2026-06-30"
   */
  setDay(dateStr: string) {
    const [y, m, d] = dateStr.split('-').map(Number)
    const mk = (h: number, min: number) => new Date(y, m - 1, d, h, min).getTime()
    this.segments = [
      { startMs: mk(9, 30), endMs: mk(11, 30), startH: 9, startM: 30, endH: 11, endM: 30 },
      { startMs: mk(13, 0), endMs: mk(15, 0),  startH: 13, startM: 0, endH: 15, endM: 0 },
    ]
    // 两段各 2h = 总共 14400s
    this.totalSegmentMs = this.segments.reduce((s, seg) => s + (seg.endMs - seg.startMs), 0)
  }

  // [废弃?] setPeriod 被 setMinPeriod 替代, 保留以防后续需要
  setPeriod(p: string) {
    this.periodMs = parseInt(p) * 60 * 1000
  }

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    this.ctx = canvas.getContext("2d")!
    this.dpr = window.devicePixelRatio || 1
  }

  /**
   * 根据容器尺寸重新设定 canvas 像素缓冲区。
   * canvas.width/height = CSS尺寸 × DPR, 通过 ctx.setTransform 让后续所有
   * 绘制坐标依旧使用 CSS 像素, 引擎内部无需关心 DPR。
   */
  resize() {
    this.dpr = window.devicePixelRatio || 1
    const parent = this.canvas.parentElement
    if (!parent) return
    const rect = parent.getBoundingClientRect()
    this.w = rect.width
    this.h = rect.height
    if (this.w === 0 || this.h === 0) return
    this.canvas.width = this.w * this.dpr
    this.canvas.height = this.h * this.dpr
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
    this.chartH = this.h * (1 - VOL_RATIO) - PADDING.bottom    // 蜡烛区 = 78% - 底部留白
    this.volH = this.h * VOL_RATIO - PADDING.bottom             // 成交量区 = 22% - 底部留白
  }

  /**
   * 每帧调用, 按层级顺序渲染全部元素。
   * @param candles 当前所有蜡烛数据(已建段的会按活跃时间映射; 未建段则线性映射)
   */
  render(candles: Candle[]) {
    // 尺寸检查
    if (this.w === 0 || this.h === 0) this.resize()
    if (this.w === 0 || this.h === 0) return

    const ctx = this.ctx
    ctx.clearRect(0, 0, this.w, this.h)
    ctx.fillStyle = this.colors.bg
    ctx.fillRect(0, 0, this.w, this.h)

    this.chartW = this.w - PADDING.left - PADDING.right

    // 1. 网格 + 边框
    this.drawGrid(ctx)
    this.drawBorder(ctx)

    if (candles.length === 0) {
      this.drawPlaceholder(ctx)
      return
    }

    // 2. 计算价格范围 (含 5% 上下 padding)
    const { minPrice, maxPrice } = this.computePriceRange(candles)
    this.lastMinPrice = minPrice
    this.lastMaxPrice = maxPrice

    // 价格轴手动覆盖优先
    const emin = this.priceMinOverride > 0 ? this.priceMinOverride : minPrice
    const emax = this.priceMaxOverride > 0 ? this.priceMaxOverride : maxPrice
    const bodyW = CANDLE_BODY_W

    // 3. 成交量 + 蜡烛 + 时间轴 + 价格轴
    this.drawVolumeBars(ctx, candles, bodyW)
    this.drawCandles(ctx, candles, bodyW, emin, emax)
    this.drawTimeAxis(ctx, candles)
    this.drawPriceAxis(ctx, emin, emax)

    // 4. 十字光标 (鼠标悬停)
    if (this.showCrosshair) {
      this.drawCrosshair(ctx)
    }
    // 5. 蜡烛点击弹框
    if (this.selectedCandle) {
      this.drawTooltip(ctx, this.selectedCandle)
    }
  }

  /**
   * 重置视口到全部数据的首尾。
   * 有 segments 时用段的首尾; 否则用蜡烛数据的首尾。
   */
  resetViewport(candles: Candle[]) {
    if (this.segments.length > 0) {
      this.viewStartMs = this.segments[0].startMs
      this.viewEndMs = this.segments[this.segments.length - 1].endMs
      return
    }
    if (candles.length > 0) {
      this.viewStartMs = candles[0].ts
      this.viewEndMs = candles[candles.length - 1].ts
    }
  }

  /**
   * 鼠标滚轮缩放 (时间轴)。
   * scale > 1 放大, < 1 缩小。
   * 最小视口范围 = periodMs × 16, 防止无限放大。
   */
  zoomAt(mouseCanvasX: number, scale: number) {
    const ts = this.xToTs(mouseCanvasX)
    const range = this.viewEndMs - this.viewStartMs
    const minRange = this.periodMs * 16
    const newRange = Math.max(range / scale, minRange)
    const ratio = range > 0 ? (ts - this.viewStartMs) / range : 0.5
    this.viewStartMs = ts - newRange * ratio
    this.viewEndMs = ts + newRange * (1 - ratio)
    if (this.segments.length > 0) this.clampViewport()
  }

  /**
   * 鼠标拖拽平移 (时间轴)。
   * deltaX 为正 = 鼠标向右移动 = 视口向左平移(看更早的数据)。
   * 有 segments 时用活跃交易时间换算; 无 segments 时用墙钟时间。
   */
  pan(deltaX: number) {
    const range = this.segments.length > 0
      ? this._visibleTradingMs()                     // 当前视口内覆盖的活跃交易时长
      : (this.viewEndMs - this.viewStartMs)           // 无段时直接用墙钟
    if (range <= 0) return
    const pxPerMs = this.chartW / range               // 1ms 对应多少像素
    const deltaMs = -deltaX / pxPerMs                 // 像素增量 → 时间增量
    this.viewStartMs += deltaMs
    this.viewEndMs += deltaMs
    if (this.segments.length > 0) this.clampViewport()
  }

  /**
   * 计算当前视口内覆盖的活跃交易毫秒数 (跳过 gaps)。
   */
  private _visibleTradingMs(): number {
    let ms = 0
    for (const seg of this.segments) {
      const s = Math.max(seg.startMs, this.viewStartMs)
      const e = Math.min(seg.endMs, this.viewEndMs)
      if (s < e) ms += e - s
    }
    return ms
  }

  /**
   * 限制视口: 最小范围 ≥ periodMs×16, 视口不得超出段首尾。
   */
  private clampViewport() {
    if (this.segments.length === 0) return
    const absStart = this.segments[0].startMs
    const absEnd = this.segments[this.segments.length - 1].endMs
    const minRange = this.periodMs * 16
    // 太窄 → 扩展
    if (this.viewEndMs - this.viewStartMs < minRange) {
      const mid = (this.viewStartMs + this.viewEndMs) / 2
      this.viewStartMs = mid - minRange / 2
      this.viewEndMs = mid + minRange / 2
    }
    // 偏左 → 右移
    if (this.viewStartMs < absStart) {
      const shift = absStart - this.viewStartMs
      this.viewStartMs += shift; this.viewEndMs += shift
    }
    // 偏右 → 左移
    if (this.viewEndMs > absEnd) {
      const shift = this.viewEndMs - absEnd
      this.viewStartMs -= shift; this.viewEndMs -= shift
    }
    // 二次保护(防止移动后仍然越界)
    if (this.viewStartMs < absStart) this.viewStartMs = absStart
    if (this.viewEndMs > absEnd) this.viewEndMs = absEnd
  }

  /**
   * 价格轴缩放 (Shift+滚轮), 以鼠标 y 坐标为中心。
   */
  zoomPriceAt(mouseY: number, scale: number) {
    if (this.lastMinPrice >= this.lastMaxPrice) return
    const range = this.lastMaxPrice - this.lastMinPrice
    const ratio = (this.chartH + PADDING.top - mouseY) / this.chartH
    const centerPrice = this.lastMinPrice + ratio * range
    const newRange = range * scale
    this.priceMinOverride = centerPrice - newRange * ratio
    this.priceMaxOverride = centerPrice + newRange * (1 - ratio)
  }

  /**
   * 价格轴拖拽平移 (Shift+鼠标拖拽)。
   */
  panPrice(deltaY: number) {
    if (this.lastMinPrice >= this.lastMaxPrice) return
    const range = this.lastMaxPrice - this.lastMinPrice
    const shift = (deltaY / this.chartH) * range
    this.priceMinOverride += shift
    this.priceMaxOverride += shift
  }

  /** 重置价格轴为自动范围 */
  resetPriceRange() {
    this.priceMinOverride = 0
    this.priceMaxOverride = 0
  }

  /**
   * 处理画布点击: 检测最近蜡烛 (距离 < 20px)。
   * @returns 点中返回 true, 点空白返回 false
   */
  handleClick(x: number, y: number, candles: Candle[]): boolean {
    this.selectedCandle = null
    if (candles.length === 0) return false
    let nearest: Candle | null = null
    let minDist = Infinity
    for (const c of candles) {
      const cx = this.tsToX(c.ts)
      const d = Math.abs(cx - x)
      if (d < minDist && d < 20) { minDist = d; nearest = c }
    }
    if (nearest) {
      this.selectedCandle = nearest
      return true
    }
    return false
  }

  /**
   * 获取十字光标处的数据 (价格 + 最近蜡烛 + 时间戳)
   * 由 KLineChart.vue 每帧调用并 emit 给父组件
   */
  getCrosshairData(candles: Candle[]): CrosshairData | null {
    if (!this.showCrosshair) return null
    const mx = this.mouseX
    const my = this.mouseY
    if (mx < PADDING.left || mx > PADDING.left + this.chartW) return null
    const range = this.lastMaxPrice - this.lastMinPrice
    const price = range > 0 ? this.lastMaxPrice - ((my - PADDING.top) / this.chartH) * range : 0
    const ts = this.xToTs(mx)
    // 找最近蜡烛(距离 < 12px)
    let nearest: Candle | null = null
    let minDist = Infinity
    for (const c of candles) {
      const cx = this.tsToX(c.ts)
      const d = Math.abs(cx - mx)
      if (d < minDist && d < 12) { minDist = d; nearest = c }
    }
    return { ts, price, candle: nearest }
  }

  // ==========================================================
  // 坐标映射
  // ==========================================================

  /**
   * 时间戳 → Canvas x 像素
   *
   * 分段模式: 只累加视口内有效交易时长, gaps 不占像素。
   * 超出视口的蜡烛会线性外推 (负 x 或 > chartW), 不会堆叠在边界。
   * 无段模式: 线性映射。
   */
  private tsToX(ts: number): number {
    if (this.segments.length === 0) {
      const range = this.viewEndMs - this.viewStartMs
      if (range <= 0) return PADDING.left + this.chartW / 2
      return PADDING.left + ((ts - this.viewStartMs) / range) * this.chartW
    }

    let totalMs = 0
    for (const seg of this.segments) {
      const s = Math.max(seg.startMs, this.viewStartMs)
      const e = Math.min(seg.endMs, this.viewEndMs)
      if (s >= e) continue
      totalMs += e - s
    }
    if (totalMs <= 0) return PADDING.left + this.chartW / 2

    let cumMs = 0
    let found = false

    for (const seg of this.segments) {
      const s = Math.max(seg.startMs, this.viewStartMs)
      const e = Math.min(seg.endMs, this.viewEndMs)
      if (s >= e) continue
      const dur = e - s

      if (ts < s) {
        cumMs += ts - s                   // 外推: 视口左侧之外 → 负值
        found = true
        break
      } else if (ts <= e) {
        cumMs += ts - s                   // 在段内: 正常累加
        found = true
        break
      } else {
        cumMs += dur                      // ts 在此段之后: 计入全段
      }
    }

    if (!found) {
      // ts > 最后可见段的结尾 → 外推右侧
      const lastE = Math.min(this.segments[this.segments.length - 1].endMs, this.viewEndMs)
      cumMs += ts - lastE
    }

    return PADDING.left + (cumMs / totalMs) * this.chartW
  }

  /**
   * Canvas x → 时间戳 (tsToX 的反函数)
   *
   * 分段模式: 反查 x 落在哪个段的哪个位置。
   * 超出视口左侧 → 外推返回 viewStartMs 之前的时刻。
   * 超出视口右侧 → 外推返回 viewEndMs 之后的时刻。
   */
  private xToTs(x: number): number {
    if (this.segments.length === 0) {
      const range = this.viewEndMs - this.viewStartMs
      return this.viewStartMs + ((x - PADDING.left) / this.chartW) * range
    }

    let totalMs = 0
    for (const seg of this.segments) {
      const s = Math.max(seg.startMs, this.viewStartMs)
      const e = Math.min(seg.endMs, this.viewEndMs)
      if (s >= e) continue
      totalMs += e - s
    }
    if (totalMs <= 0) return this.viewStartMs

    const targetMs = ((x - PADDING.left) / this.chartW) * totalMs

    // 外推左侧: targetMs < 0 → 返回 viewStartMs - 偏移
    if (targetMs < 0) return this.viewStartMs + targetMs

    let cumMs = 0
    for (const seg of this.segments) {
      const s = Math.max(seg.startMs, this.viewStartMs)
      const e = Math.min(seg.endMs, this.viewEndMs)
      if (s >= e) continue
      const dur = e - s
      if (targetMs >= cumMs && targetMs <= cumMs + dur) {
        return s + (targetMs - cumMs)
      }
      cumMs += dur
    }

    // 外推右侧: targetMs > totalMs → 返回 viewEndMs + 超出的偏移
    return this.viewEndMs + (targetMs - totalMs)
  }

  /**
   * 价格 → Canvas y 坐标 (线性: 顶=最高价, 底=最低价)
   */
  private yFromPrice(price: number, minPrice: number, maxPrice: number): number {
    const range = maxPrice - minPrice
    return PADDING.top + this.chartH - ((price - minPrice) / range) * this.chartH
  }

  // ==========================================================
  // 价格计算
  // ==========================================================

  /** 从蜡烛数组计算可视价格范围, 上下留 5% padding */
  private computePriceRange(candles: Candle[]) {
    let minP = Infinity, maxP = -Infinity
    for (const c of candles) {
      if (c.low < minP) minP = c.low
      if (c.high > maxP) maxP = c.high
    }
    if (!isFinite(minP)) return { minPrice: 0, maxPrice: 100 }
    if (minP === maxP) { minP *= 0.95; maxP *= 1.05 }
    const pad = (maxP - minP) * 0.05
    return { minPrice: minP - pad, maxPrice: maxP + pad }
  }

  // ==========================================================
  // 绘制方法 (按渲染层级)
  // ==========================================================

  /** K线区 6 条横线; 成交量区 3 条横线 */
  private drawGrid(ctx: CanvasRenderingContext2D) {
    ctx.strokeStyle = this.colors.grid
    ctx.lineWidth = 1
    for (let i = 0; i <= 5; i++) {
      const y = PADDING.top + (this.chartH * i) / 5
      ctx.beginPath(); ctx.moveTo(PADDING.left, y); ctx.lineTo(PADDING.left + this.chartW, y); ctx.stroke()
    }
    for (let i = 0; i <= 2; i++) {
      const y = this.chartH + PADDING.top + (this.volH * i) / 2
      ctx.beginPath(); ctx.moveTo(PADDING.left, y); ctx.lineTo(PADDING.left + this.chartW, y); ctx.stroke()
    }
  }

  /** 图表外框 */
  private drawBorder(ctx: CanvasRenderingContext2D) {
    ctx.strokeStyle = this.colors.axisLine
    ctx.strokeRect(PADDING.left, PADDING.top, this.chartW, this.chartH + this.volH)
  }

  /** 无数据占位 */
  private drawPlaceholder(ctx: CanvasRenderingContext2D) {
    ctx.fillStyle = this.colors.text
    ctx.font = "13px -apple-system, sans-serif"
    ctx.textAlign = "center"
    ctx.textBaseline = "middle"
    // ctx.fillText("暂无数据", PADDING.left + this.chartW / 2, PADDING.top + this.chartH / 2)
  }

  /**
   * 绘制历史蜡烛 (红涨绿跌, 实体 + 上下影线)。
   * 超出图表区域 ±10px 的蜡烛跳过不绘。
   */
  private drawCandles(ctx: CanvasRenderingContext2D, candles: Candle[], bodyW: number, minP: number, maxP: number) {
    for (const c of candles) {
      const x = this.tsToX(c.ts)
      // 裁剪: 超出可视区域的不画
      if (x < PADDING.left - 10 || x > PADDING.left + this.chartW + 10) continue

      const isUp = c.close >= c.open
      ctx.strokeStyle = isUp ? this.colors.up : this.colors.down
      ctx.fillStyle = isUp ? this.colors.up : this.colors.down
      ctx.lineWidth = 1

      // 影线
      ctx.beginPath()
      ctx.moveTo(x, this.yFromPrice(c.high, minP, maxP))
      ctx.lineTo(x, this.yFromPrice(c.low, minP, maxP))
      ctx.stroke()

      // 实体
      const yO = this.yFromPrice(c.open, minP, maxP)
      const yC = this.yFromPrice(c.close, minP, maxP)
      ctx.fillRect(x - bodyW / 2, Math.min(yO, yC), bodyW, Math.max(1, Math.abs(yC - yO)))
    }
  }

  /** 成交量柱 (红涨绿跌, 底部对齐, 高度正比于成交量) */
  private drawVolumeBars(ctx: CanvasRenderingContext2D, candles: Candle[], bodyW: number) {
    const base = this.chartH + PADDING.top          // 成交量区顶部 = 蜡烛区底部
    let maxVol = 0
    for (const c of candles) { if (c.volume > maxVol) maxVol = c.volume }
    if (maxVol === 0) return
    for (const c of candles) {
      const x = this.tsToX(c.ts)
      if (x < PADDING.left - 10 || x > PADDING.left + this.chartW + 10) continue
      const h = Math.max(1, (c.volume / maxVol) * this.volH)
      ctx.fillStyle = c.close >= c.open ? this.colors.volUp : this.colors.volDown
      ctx.fillRect(x - bodyW / 2, base + this.volH - h, bodyW, h)
    }
  }

  /** 左侧价格轴, 6 档刻度 */
  private drawPriceAxis(ctx: CanvasRenderingContext2D, minP: number, maxP: number) {
    ctx.fillStyle = this.colors.textBright
    ctx.font = "11px monospace"
    ctx.textAlign = "left"
    ctx.textBaseline = "middle"
    const range = maxP - minP
    for (let i = 0; i <= 5; i++) {
      const price = maxP - (range * i) / 5
      ctx.fillText(price.toFixed(2), 4, this.yFromPrice(price, minP, maxP))
    }
  }

  /**
   * 底部时间轴。
   * 两段模式: 显示 09:30, 11:30/13:00(断点), 15:00
   * 无段模式: 从蜡烛等间隔取 6 个标签
   * 超出图表区域 ±50px 的标签不画
   */
  private drawTimeAxis(ctx: CanvasRenderingContext2D, candles: Candle[]) {
    ctx.fillStyle = this.colors.textBright
    ctx.font = "bolder 16px consolas"
    ctx.textAlign = "center"
    const ay = this.h - PADDING.bottom

    // 两段模式
    if (this.segments.length >= 2 && this._visibleTradingMs() > 0) {
      const firstSeg = this.segments[0]
      const lastSeg = this.segments[this.segments.length - 1]
      const xFirst = this.tsToX(firstSeg.startMs)
      const xBreak = this.tsToX(firstSeg.endMs)
      const xLast = this.tsToX(lastSeg.endMs)

      const startLabel = `${String(firstSeg.startH).padStart(2,'0')}:${String(firstSeg.startM).padStart(2,'0')}`
      const endLabel = `${String(lastSeg.endH).padStart(2,'0')}:${String(lastSeg.endM).padStart(2,'0')}`
      const breakLabel = `${String(firstSeg.endH).padStart(2,'0')}:${String(firstSeg.endM).padStart(2,'0')}/${String(lastSeg.startH).padStart(2,'0')}:${String(lastSeg.startM).padStart(2,'0')}`

      // 只在可视区域内绘制, 防标签堆叠到图表外
      const inView = (x: number) => x > PADDING.left - 50 && x < PADDING.left + this.chartW + 50
      const gapW = Math.abs(xBreak - xLast)            // 断点与末尾的距离, 太近跳过防重叠
      if (inView(xFirst)) ctx.fillText(startLabel, xFirst, ay)
      if (inView(xBreak) && gapW > 8) ctx.fillText(breakLabel, xBreak, ay)
      if (inView(xLast)) ctx.fillText(endLabel, xLast, ay)
      return
    }

    // 兜底: 无段时等间隔取蜡烛时间
    if (candles.length > 0) {
      const step = Math.max(1, Math.floor(candles.length / 6))
      for (let i = 0; i < candles.length; i += step) {
        const x = this.tsToX(candles[i].ts)
        if (x < PADDING.left - 40 || x > PADDING.left + this.chartW + 40) continue
        const d = new Date(candles[i].ts)
        ctx.fillText(`${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`, x, ay)
      }
    }
  }

  /**
   * 十字光标: 虚线竖线(时间) + 虚线横线(价格);
   * 底部时间标签 + 左侧价格标签
   */
  private drawCrosshair(ctx: CanvasRenderingContext2D) {
    const { mouseX: mx, mouseY: my } = this
    if (mx < PADDING.left || mx > PADDING.left + this.chartW) return
    ctx.save()
    ctx.strokeStyle = this.colors.crosshair
    ctx.lineWidth = 1
    ctx.setLineDash([4, 4])
    ctx.beginPath(); ctx.moveTo(mx, PADDING.top); ctx.lineTo(mx, this.chartH + PADDING.top + this.volH); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(PADDING.left, my); ctx.lineTo(PADDING.left + this.chartW, my); ctx.stroke()
    ctx.setLineDash([])

    // 时间标签
    const ts = this.xToTs(mx)
    const d = new Date(ts)
    const timeLabel = `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`
    const tw = ctx.measureText(timeLabel).width + 12
    const ly = this.h - PADDING.bottom + 2
    ctx.fillStyle = this.colors.crosshairLabel
    ctx.fillRect(mx - tw / 2, ly, tw, 18)
    ctx.fillStyle = this.colors.textWhite
    ctx.font = "12px monospace"
    ctx.textAlign = "center"
    ctx.fillText(timeLabel, mx, ly + 13)

    // 价格标签
    const price = this.lastMinPrice + ((this.chartH + PADDING.top - my) / this.chartH) * (this.lastMaxPrice - this.lastMinPrice)
    const label = price.toFixed(2)
    const plw = ctx.measureText(label).width + 10
    ctx.fillStyle = this.colors.crosshairLabel
    ctx.fillRect(PADDING.left - plw - 4, my - 10, plw, 18)
    ctx.fillStyle = this.colors.textWhite
    ctx.font = "600 12px monospace"
    ctx.textAlign = "right"
    ctx.fillText(label, PADDING.left - 6, my + 3)
    ctx.restore()
  }

  /**
   * 蜡烛点击弹框: 高/低/开/收 四行
   * 默认在蜡烛右上方; 靠近右边界时改左上方; 靠上边界时改下方
   */
  private drawTooltip(ctx: CanvasRenderingContext2D, candle: Candle) {
    const cx = this.tsToX(candle.ts)
    const rows = [
      `高: ${candle.high.toFixed(2)}`,
      `低: ${candle.low.toFixed(2)}`,
      `开: ${candle.open.toFixed(2)}`,
      `收: ${candle.close.toFixed(2)}`,
    ]
    ctx.font = "bold 13px monospace"
    const rowH = 19, padX = 10, padY = 6
    const rowsH = rows.length * rowH + padY * 2
    let maxW = 0
    for (const r of rows) { const w = ctx.measureText(r).width; if (w > maxW) maxW = w }
    const rectW = maxW + padX * 2

    // 默认: 蜡烛右上方
    let tx = cx + 8
    let ty = this.yFromPrice(candle.high, this.lastMinPrice, this.lastMaxPrice) - rowsH - 6

    // 右边界保护 → 改左上方
    if (tx + rectW > this.w - PADDING.right - 4) tx = cx - rectW - 8
    if (tx < PADDING.left + 2) tx = PADDING.left + 2

    // 上边界保护 → 改蜡烛下方
    if (ty < PADDING.top + 2) ty = this.yFromPrice(candle.low, this.lastMinPrice, this.lastMaxPrice) + padY
    // 下边界保护
    if (ty + rowsH > this.h - PADDING.bottom - 2) ty = this.h - PADDING.bottom - rowsH - 2

    // 半透明暗底 + 细边框 + 亮色文字
    ctx.fillStyle = "rgba(255,255,255,0.57)"
    ctx.fillRect(tx, ty, rectW, rowsH)
    ctx.strokeStyle = this.colors.bg
    ctx.lineWidth = 1
    ctx.strokeRect(tx, ty, rectW, rowsH)
    ctx.fillStyle = this.colors.textBright
    ctx.textAlign = "left"
    ctx.textBaseline = "middle"
    for (let i = 0; i < rows.length; i++) {
      ctx.fillText(rows[i], tx + padX, ty + padY + rowH / 2 + i * rowH)
    }
  }
}
