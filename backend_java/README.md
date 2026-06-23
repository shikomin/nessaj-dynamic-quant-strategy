# Java 后端 — SpringBoot 业务中枢

> 负责业务逻辑、用户鉴权、Python 模型调用、实盘模拟、WebSocket 实时推送

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 框架 | SpringBoot 2.7+ | Java 17 |
| ORM | MyBatis-Plus | MySQL 8.0 |
| 时序查询 | TDengine REST/taos-jdbcdriver | 行情数据 |
| 缓存 | Redis | 策略参数缓存 |
| 鉴权 | Spring Security + JWT | 登录/注册/权限 |
| 推送 | WebSocket (STOMP) | 实时行情+策略变更 |
| HTTP 客户端 | RestTemplate | 调用 Python FastAPI |

## 模块划分

```
src/main/java/com/nessaj/
├── common/
│   ├── R.java                    # 统一响应体 {code, msg, data}
│   ├── GlobalExceptionHandler    # 全局异常拦截
│   ├── CorsConfig                # 跨域配置
│   └── JwtUtil                   # Token 生成/校验
├── auth/
│   ├── LoginController           # POST /api/auth/login
│   ├── RegisterController        # POST /api/auth/register
│   ├── JwtTokenFilter            # 请求拦截 → 校验 Token
│   ├── UserService               # 用户 CRUD
│   └── UserEntity                # id/username/password/status
├── strategy/
│   ├── StrategyController        # GET/PUT /api/strategy 策略开关/手动调参
│   ├── StrategyCacheService      # Redis 读写当前策略+参数
│   └── StrategyVO                # {strategyId, p1..p5, updateTime}
├── inference/
│   ├── InferenceClient           # RestTemplate POST /predict → FastAPI
│   ├── InferenceScheduler        # 定时任务(每分钟) → 拉全量标的推理
│   └── FallbackHandler           # 模型不可用时返回保守参数
├── market/
│   ├── MarketDataService         # TDengine 查询最近 K 线 + 特征
│   ├── FeatureCalculator         # Java 轻量版 26 维特征计算
│   └── SentimentService          # 市场情绪查询
├── trade/
│   ├── TradeSimulator            # 根据策略+参数 + 实时行情模拟买卖
│   ├── PositionManager           # 持仓跟踪 + 盈亏计算
│   └── RiskController            # T+1/涨跌停/最大仓位限制
├── websocket/
│   ├── MarketWebSocket           # /ws/market   行情推送
│   ├── StrategyWebSocket         # /ws/strategy 策略变更推送
│   └── TradeWebSocket            # /ws/trade    成交/持仓推送
└── entity/
    ├── StockInfo                 # 股票基础信息
    ├── KLine                     # K 线数据
    └── TradeRecord               # 交易记录
```

## 核心 API

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/api/auth/login` | 登录 → JWT | 无 |
| POST | `/api/auth/register` | 注册 | 无 |
| GET  | `/api/strategy` | 当前策略+参数 | JWT |
| PUT  | `/api/strategy` | 手动修改策略/参数 | JWT |
| GET  | `/api/market/kline/{code}` | 最近 K 线数据 | JWT |
| GET  | `/api/trade/positions` | 当前持仓 | JWT |
| GET  | `/api/trade/history` | 历史交易记录 | JWT |

## 核心数据流

```
1. InferenceScheduler (每分钟)
   TDengine → 取每只股最近240条K线
           → FeatureCalculator 计算26维特征
           → InferenceClient POST /predict
           → Python FastAPI 返回 {strategyId, p1..p5}
           → StrategyCacheService 写入 Redis
           → StrategyWebSocket 推送前端

2. TradeSimulator
   实时行情(tick/1min) → 当前策略+参数 → 模拟买卖
                      → PositionManager 更新持仓
                      → TradeWebSocket 推送前端

3. 容错
   InferenceClient 超时/500 → FallbackHandler 返回保守参数
   Python 服务挂了 → 继续用 Redis 缓存的上一组参数
```

## 配置示例 (application.yml)

```yaml
server:
  port: 8080

spring:
  datasource:
    url: jdbc:mysql://localhost:3306/nessaj
    username: root
    password: ${MYSQL_PASSWORD}
  redis:
    host: localhost
    port: 6379

tdengine:
  url: jdbc:TAOS://localhost:6030/quant_dynamic
  username: root
  password: ${TDENGINE_PASSWORD}

python:
  inference:
    url: http://localhost:8000/predict
    timeout: 5000
    retry: 3

jwt:
  secret: ${JWT_SECRET}
  expiration: 86400000  # 24h
```
