# 前端 — Vue3 + Electron 桌面客户端

> K线可视化、策略参数面板、实时推送展示、回测进度监控

## 技术栈

| 组件 | 技术 |
|------|------|
| 框架 | Vue3 (Composition API) + TypeScript |
| 构建 | Vite 5 |
| 桌面壳 | Electron 28+ |
| 图表 | ECharts 5 |
| 状态管理 | Pinia |
| HTTP | Axios (拦截器封装) |
| WebSocket | STOMP.js / SockJS |
| UI 组件 | 自建组件 (或 Element Plus) |
| 路由 | Vue Router 4 |
| 样式 | CSS Variables + Scoped |

## 页面路由

```
/login              # 登录页
/register           # 注册页

/dashboard          # 首页仪表盘
  └─ 策略总览卡片、今日收益、持仓汇总、大盘情绪指数

/market             # 实时行情
  ├─ 股票列表 (搜索/筛选)
  └─ /market/:code  # 单股详情
       └─ K线图 (日线/分钟线切换)
       └─ 叠加显示: 当前策略信号标记 (买/卖点)
       └─ 叠加显示: 动态均线/布林带

/strategy           # 策略面板
  ├─ 当前激活策略 + 5参数实时数值
  ├─ 5策略列表 (可折叠, 高亮当前)
  ├─ 参数历史变化折线图
  └─ 手动调参入口

/backtest           # 回测
  ├─ 回测进度条 (百分比 + 已完成窗口数)
  ├─ 历史回测结果列表
  └─ 单次回测详情 (Calmar/Sharpe/回撤/收益曲线)

/history            # 历史表现
  ├─ 累计收益曲线
  ├─ 最大回撤曲线
  ├─ 月度收益热力图
  └─ 交易记录表

/settings           # 设置
  ├─ Python 推理服务地址
  ├─ 数据源选择
  └─ 主题切换 (暗色/亮色)
```

## 核心组件树

```
src/
├── App.vue
├── main.ts                    # 入口 + Pinia + Router + Axios 配置
├── router/
│   └── index.ts               # 路由守卫 (未登录 → /login)
├── stores/
│   ├── auth.ts                # JWT Token + 用户信息
│   ├── strategy.ts            # 当前策略+参数 (WebSocket 更新)
│   ├── market.ts              # 行情数据 (WebSocket 更新)
│   ├── position.ts            # 持仓数据 (WebSocket 更新)
│   └── settings.ts            # 用户偏好 (主题/地址)
├── api/
│   ├── request.ts             # Axios 实例 (baseURL + JWT 拦截 + 401 刷新)
│   ├── auth.ts                # login/register
│   ├── strategy.ts            # getStrategy/updateStrategy
│   ├── market.ts              # getKLine/getFeatures
│   └── trade.ts               # getPositions/getHistory
├── composables/
│   ├── useWebSocket.ts        # STOMP 连接/订阅/断开
│   └── useKLineChart.ts       # ECharts K线图 Hook
├── views/
│   ├── LoginView.vue
│   ├── RegisterView.vue
│   ├── DashboardView.vue
│   ├── MarketView.vue
│   ├── MarketDetailView.vue
│   ├── StrategyView.vue
│   ├── BacktestView.vue
│   ├── HistoryView.vue
│   └── SettingsView.vue
├── components/
│   ├── layout/
│   │   ├── AppLayout.vue      # 整体布局 (侧边栏 + 顶栏 + 内容区)
│   │   ├── SideMenu.vue       # 左侧导航菜单
│   │   └── TopBar.vue         # 顶栏 (用户头像 + 通知 + 退出)
│   ├── chart/
│   │   ├── KLineChart.vue     # ECharts K线图 (叠加策略标记)
│   │   ├── ProfitCurve.vue    # 收益曲线
│   │   ├── DrawdownChart.vue  # 回撤图
│   │   └── StrategyOverlay.vue # K线上的策略信号叠加层
│   ├── strategy/
│   │   ├── ParamPanel.vue     # 5策略参数实时面板
│   │   ├── ParamHistory.vue   # 参数历史变化折线图
│   │   └── StrategyBadge.vue  # 当前激活策略标签 (带颜色)
│   ├── backtest/
│   │   ├── ProgressBar.vue    # 回测进度条
│   │   └── ResultCard.vue     # 回测结果卡片
│   ├── trade/
│   │   ├── PositionTable.vue  # 持仓列表
│   │   └── TradeHistory.vue   # 交易记录表
│   └── common/
│       ├── StockSelector.vue  # 股票搜索/选择 (联想输入)
│       ├── DataTable.vue      # 通用数据表格 (排序/分页)
│       └── StatusDot.vue      # 状态指示灯 (绿/黄/红)
└── assets/
    ├── variables.css           # CSS 变量 (主题色/字体/间距)
    └── logo.svg
```

## WebSocket 消息协议

```typescript
// 服务端 → 客户端
type WsMessage =
  | { type: 'kline',    code: string, data: KLine[] }
  | { type: 'strategy', code: string, strategyId: number, params: number[] }
  | { type: 'position', positions: Position[] }
  | { type: 'trade',    trade: TradeRecord }

// 客户端 → 服务端
type WsSubscribe = { action: 'subscribe', channel: string }
```

## Electron 桌面壳配置

```javascript
// electron/main.js
const { app, BrowserWindow, Tray, Menu } = require('electron')

function createWindow() {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 680,
    webPreferences: { nodeIntegration: false, contextIsolation: true }
  })
  win.loadURL('http://localhost:5173') // 开发环境
  // win.loadFile('dist/index.html')   // 生产打包
}

// 系统托盘 → 最小化到托盘, 双击恢复
// 关闭窗口 → 隐藏而非退出
// 应用退出 → 清理 WebSocket 连接
```

## Axios 封装示例

```typescript
// api/request.ts
import axios from 'axios'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080/api',
  timeout: 10000,
})

// 请求拦截: 自动附带 JWT
http.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截: 401 → 跳登录
http.interceptors.response.use(
  res => res.data,
  err => {
    if (err.response?.status === 401) window.location.href = '/login'
    return Promise.reject(err)
  }
)

export default http
```

## Pinia Store 示例 (strategy)

```typescript
// stores/strategy.ts
import { defineStore } from 'pinia'

export const useStrategyStore = defineStore('strategy', {
  state: () => ({
    current: { strategyId: 0, p1: 0, p2: 0, p3: 0, p4: 0.05, p5: 240 },
    history: [] as { ts: string; strategyId: number; params: number[] }[],
  }),
  actions: {
    updateFromWs(data: any) {
      this.current = { strategyId: data.strategyId, ...data.params }
      this.history.push({ ts: Date.now().toString(), ...this.current })
    },
    async fetchFromApi(code: string) {
      const res = await import('@/api/strategy').then(m => m.getStrategy(code))
      this.current = res.data
    },
  },
})
```
