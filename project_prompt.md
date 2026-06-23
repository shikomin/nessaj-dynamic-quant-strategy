# A股动态参数量化交易系统 - 项目开发文档 (Project Prompt)

> **修订版 v2.1** — 2026-06-22  
> Phase 01 完成：数据管线跑通，100只股1分钟线+26维特征入库  
> 设计演进：Plan B 负采样、stride=60、Pairwise Ranking Loss

---

## 1. 项目概述 (Project Overview)

### 1.1 核心理念
本项目构建一个 **"多策略动态参数量化交易系统"** 。核心思想：

1. **不做价格预测** — 不预测涨跌，而是预测"当前市场状态下，哪种策略 + 哪组参数最有效"
2. **多策略覆盖** — 不同市况（趋势/震荡/高波/放量）用不同策略模板，模型自适应选择
3. **分钟级粒度** — 用分钟线生成训练样本，数据量从 ~2000 条跃升到 40万+ 条

### 1.2 原方案遗留问题

| # | 硬伤 | 问题 | 修复 |
|---|------|------|------|
| 1 | **日线训练样本严重不足** | 日线 3天特征+20天回测窗口，每只股仅20个样本，100只=2000条，LSTM无法泛化 | 改分钟线：240条特征(1天)+240-480条回测(1-2天)，100只=40万样本 |
| 2 | **单一策略模板** | 双均线只适用于趋势市，震荡/高波/量异动市况下失效 | 扩展到5个策略模板，模型同时输出策略选择+参数 |
| 3 | **特征维度太少** | OHLCV+Amount(6维)不足以描述市场结构 | 扩充到**26维**：25维技术指标 + 1维市场情绪 |
| 4 | **日线/分钟线割裂** | 设计中日线训练→分钟线推理，特征分布不一致 | 统一用分钟线：训练(分钟线生成样本)+推理(最近1天分钟线) |
| 5 | **参数稳定性未考虑** | 模型单次推理参数可能日间剧烈跳变，无法实盘 | 加入参数平滑(EMA)、参数变化幅度惩罚 |
| 6 | **无数据积累策略** | 东方财富分钟线API仅返回5天，无法获得更长历史 | 接入 **zzshare** (自在量化)，免费获取2005年至今分钟线 + 市场情绪数据 |

### 1.3 技术架构总览
```
数据采集(zzshare→TDengine) → 特征工程(26维指标,含情绪) → 样本生成(Optuna多策略寻优→Parquet)
→ 模型训练(LSTM多任务:策略分类+参数回归) → FastAPI推理 → SpringBoot业务 → Vue前端
```

| 层级 | 技术 |
|:---|:---|
| 数据获取 | **zzshare** (自在量化) — 免费，2005年至今分钟线，60次/分钟 |
| 数据库 | TDengine 3.3.8 (时序) + MySQL 8.0 (关系) |
| 特征工程 | Pandas + TA-Lib (RSI/MACD/布林/ATR/OBV等 + 市场情绪) |
| 样本生成 | Optuna (多策略并行寻优) |
| 回测引擎 | VectorBT + 自定义向量化引擎 |
| 模型 | PyTorch LSTM + MLP 多任务学习 |
| 模型服务 | FastAPI |
| 后端 | SpringBoot 2.7+ |
| 前端 | Vue3 + Vite + TypeScript + Electron + ECharts |

### 1.4 数据流向
```
1. 数据采集: zzshare → TDengine (分钟线+日线+情绪)
2. 定时积累: 每日运行 data_fetcher.py → TDengine (增量, 100只股/分钟)
3. 特征工程: TDengine → 26维技术指标 → Parquet
4. 样本生成: Parquet → Optuna多策略回测 → (features, strategy_label, params_label) → train.parquet
5. 模型训练: train.parquet → PyTorch LSTM → best_model.pth + scaler.pkl
6. 实盘推理: 实时行情 → FastAPI (加载模型) → 策略类型 + 参数向量
7. 参数平滑: 输出参数 → EMA平滑 → 交易执行
```

---

## 2. 多策略模板设计 (Multi-Strategy Templates)

### 2.1 策略矩阵

| 策略ID | 名称 | 适用市况 | 参数 (5个) |
|--------|------|---------|-----------|
| 0 | 双均线突破 (MA Breakout) | 单边趋势 | fast_period, slow_period, stop_atr, profit_pct, max_hold |
| 1 | 布林带回归 (BB Reversal) | 震荡市 | bb_period, bb_std, stop_atr, profit_pct, max_hold |
| 2 | 放量突破 (Vol Breakout) | 行情启动 | vol_ratio, lookback, stop_atr, profit_pct, max_hold |
| 3 | ATR通道突破 (ATR Channel) | 高波动 | atr_period, atr_mult, stop_atr, profit_pct, max_hold |
| 4 | 动量突破 (Momentum) | 强势行情 | mom_period, mom_thresh, stop_atr, profit_pct, max_hold |

**5 参数解释**：
- **p1, p2**: 策略专属入场信号参数
- **p3 (stop_atr)**: ATR 倍数动态止损（自适应波动率）
- **p4 (profit_pct)**: 止盈百分比（1%~10%）
- **p5 (max_hold)** : 最大持仓分钟数（60~480），超时自动平仓

**回测模拟项**：T+1(按交易日日期分组)、千1.5手续费、0.1%滑点、涨跌停检测(涨停买不进/跌停卖不出)、停牌缺口跳过

### 2.2 参数向量定义

```python
# 每个策略5个参数
STRATEGY_PARAMS = {
    0: {"fast_period": (2, 20),   "slow_period": (20, 120), "stop_atr": (1.0, 4.0),
        "profit_pct": (0.01, 0.10), "max_hold": (60, 480)},
    1: {"bb_period": (10, 50),    "bb_std": (1.5, 3.0),    "stop_atr": (1.0, 4.0),
        "profit_pct": (0.01, 0.10), "max_hold": (60, 480)},
    2: {"vol_ratio": (1.5, 5.0),  "lookback": (5, 30),    "stop_atr": (1.0, 4.0),
        "profit_pct": (0.01, 0.10), "max_hold": (60, 480)},
    3: {"atr_period": (5, 30),    "atr_mult": (1.5, 4.0), "stop_atr": (1.0, 4.0),
        "profit_pct": (0.01, 0.10), "max_hold": (60, 480)},
    4: {"mom_period": (5, 30),    "mom_thresh": (0.02, 0.10), "stop_atr": (1.0, 4.0),
        "profit_pct": (0.01, 0.10), "max_hold": (60, 480)},
}
```

---

## 3. 数据处理与特征工程 (Data & Features)

### 3.1 数据采集策略

| 数据类型 | 频率 | 来源 | 历史长度 | 用途 |
|---------|------|------|---------|------|
| 1分钟K线 | 首次全量 + 每日增量 | **zzshare** | 2005年至今 (20年+) | 特征提取 + 样本生成 + 推理 |
| 5分钟K线 | 首次全量 + 每日增量 | zzshare | 2005年至今 | 备选特征粒度 |
| 日线K线 | 首次全量 + 每日增量 | zzshare | 2005年至今 | 辅助指标(MA20/60位置) |
| 市场情绪K线 | 每日定时 | zzshare | 全量 | 第26维情绪特征 |

**数据源切换**：从 AkShare (5天限制) 切换到 **zzshare** (自在量化)，免费 API，有 Token 下 60次/分钟速率限制。首次全量拉取 100只股 × 2年 ≈ 8小时，后续每日增量 1分钟完成。

**代码格式**：zzshare 使用 `600036.SH` / `301217.SZ` 格式，与内部 `sh600036` / `sz301217` 不同，在 fetcher 层做映射。

### 3.2 特征工程（26维输入向量）

| 类别 | 特征 | 维度 | 说明 |
|------|------|------|------|
| 价格基础 | open, high, low, close | 4 | 原始价格 (RobustScaler归一化) |
| 成交量 | volume, volume_ma5, volume_ratio | 3 | 成交量 + 5日均量 + 量比 |
| 趋势类 | ma5, ma10, ma20, ma60 | 4 | 多周期均线/当前价比值 |
| 动量类 | rsi_6, rsi_14, macd, macd_signal, macd_hist | 5 | RSI + MACD |
| 波动类 | atr_14, bb_upper, bb_lower, bb_width | 4 | ATR + 布林带 |
| 量价类 | obv, mfi_14 | 2 | OBV + 资金流量指标 |
| 市场结构 | amplitude_pct, turnover_pct, up_down_ratio | 3 | 振幅/换手率/涨跌比 |
| **情绪** | **market_sentiment** | **1** | **每日市场情绪K线值 (zzshare market_sentiment)** |

**输出**：每只股票每分钟生成一条 26 维特征向量，240条构成一天的输入序列。

### 3.3 市场情绪数据

zzshare 提供每日市场情绪K线，格式为 OHLC+K 线结构，存储于 TDengine `market_sentiment` 超级表：

| 子表 | 用途 | 数据模型 |
|------|------|------|
| `sent_daily` | `api.market_sentiment()` 每日情绪K线 | model='market_sentiment' |
| `sent_hot` | `api.market_hot_sentiment()` 热门情绪 | model='market_hot_sentiment' |
| `sent_trend` | `api.sentiment_trend()` 情绪趋势 | model='sentiment_trend' |

情绪值作为第 26 维特征，参与模型输入，让模型感知整体市场气氛（贪婪/恐惧），有助于策略类型选择（如情绪高涨时倾向动量策略，恐慌时倾向布林带反转）。

### 3.4 标准化
- 训练集拟合 `RobustScaler`（抗异常值），保存为 `models/scaler.pkl`
- 推理时加载同一 scaler 做变换

---

## 4. 训练样本生成 (Sample Generation)

### 4.1 滑动窗口设计（分钟线版本）

```
                    时间轴 →
|------ 特征窗口(1天) ------|---- 回测窗口(1-2天) ----|
         240条1分钟线             240~480条1分钟线

步长 = 60条 (1小时)，相邻窗口特征有180条重叠，但标签(回测窗口)不重叠
```

| 参数 | 值 | 说明 |
|------|-----|------|
| 特征窗口 | 240条 (1个交易日) | 26维特征，作为模型输入 |
| 回测窗口 | 240~480条 (1-2个交易日) | 短线交易周期 |
| **步长** | **60条 (1小时)** | 1小时滑动，样本量×4 |
| 数据量/股 | ~1000个窗口 (1年分钟线) | (60480-480)/60+1 |
| 100只股总窗口 | ~10万 | ×4 = 40万条训练记录 |

> **重叠窗口可行**：相邻窗口特征有重叠，但回测窗口对应不同交易日，标签完全不同。LSTM 学到"同样RSI值在不同时间点，最优策略可能不同"的时序敏感性。

### 4.2 Optuna 多策略寻优

对每个时间窗口，Optuna 搜索空间包含：
1. **策略选择**：categorical [0,1,2,3,4]
2. **对应策略的参数**：在该策略的范围内搜索

```python
def objective(trial, df_window):
    strategy_id = trial.suggest_categorical("strategy", [0,1,2,3,4])
    params = {}
    for name, (low, high) in STRATEGY_PARAMS[strategy_id].items():
        params[name] = trial.suggest_float(name, low, high)
    
    # 执行该策略在该窗口的回测
    result = backtest_engine.run(df_window, strategy_id, params)
    return result.calmar_ratio  # 最大化卡玛比率
```

**每个窗口**：Optuna 运行 **50 trials**，记录所有 (策略, 参数, Calmar) 三元组。

### 4.3 Plan B 负采样标签格式

每个窗口生成 **4条训练记录**，而非仅最优解：

| 记录 | 来源 | 权重 | 作用 |
|------|------|------|------|
| 正样本 ×1 | Calmar 最高的 trial | **1.0** | 这种行情该这样做 |
| 差负样本 ×1 | Calmar 最低的 trial | **0.1** | 千万避开这个 |
| 中负样本 ×2 | 随机中等 Calmar trials | **0.3** | 丰富负样本多样性 |

```python
samples = [
    {"features": ..., "strategy": best_sid,  "params": best_p,  "weight": 1.0},
    {"features": ..., "strategy": worst_sid, "params": worst_p, "weight": 0.1},
    {"features": ..., "strategy": mid1_sid,  "params": mid1_p,  "weight": 0.3},
    {"features": ..., "strategy": mid2_sid,  "params": mid2_p,  "weight": 0.3},
]
# params = [p1, p2, p3, p4, p5]  5个参数
```

### 4.4 输出格式
- 文件：`data/train.parquet`, `data/val.parquet`
- 按时间切分：前80%训练，后20%验证（严禁随机打乱）
- 所有浮点字段为 float32

---

## 5. 模型设计 (Model Architecture)

### 5.1 多任务 LSTM 网络

```
输入: (Batch, 240, 26)
  │
  ▼
LSTM(hidden=128, layers=2, dropout=0.3, bidirectional=True)
  │
  ▼ [取最后时间步 + 拼接双向]
Linear(256 → 128) + LayerNorm + ReLU + Dropout(0.3)
  │
  ├─── 策略分类头 ──── Linear(128 → 64) + ReLU → Linear(64 → 5) + Softmax
  │    CrossEntropyLoss
  │
  └─── 参数回归头 ──── Linear(128 → 64) + ReLU → Linear(64 → 5) + Sigmoid
       SmoothL1Loss (Huber, β=0.5)
```

### 5.2 输出设计
- **策略分类**：5类 softmax，取 argmax 为最终策略
- **参数回归**：Sigmoid 输出 [0,1]，再映射到各策略的参数定义域
- 推理时先确定策略，再用对应定义域解码参数

### 5.3 损失函数 (Plan B — 加权联合损失)

```python
def joint_loss(strategy_logits, strategy_label, param_pred, param_label, 
               calmar_weight, sample_weight):
    """
    sample_weight: Plan B 权重 (正样本=1.0, 差负=0.1, 中负=0.3)
    效果: 模型必须学会"哪种行情下什么策略是优/中/差"
    """
    # 策略分类：加权交叉熵
    ce = F.cross_entropy(strategy_logits, strategy_label, reduction='none')
    ce = (ce * sample_weight).mean()
    
    # 参数回归：Huber Loss（SmoothL1）
    huber = F.smooth_l1_loss(param_pred, param_label, beta=0.5, reduction='none')
    huber = (huber.mean(dim=1) * calmar_weight * sample_weight).mean()
    
    return ce + 0.5 * huber
```

> **设计意图**：不追求精确回归参数值（回测标签本身有噪声），而是让模型学到 **相对排序**——"策略A > 策略B > 策略C"。低权重负样本防止模型盲目模仿所有参数组合。

### 5.4 训练配置
- 优化器：AdamW (lr=1e-3, weight_decay=1e-4)
- 学习率调度：ReduceLROnPlateau (patience=10, factor=0.5)
- 早停：验证Loss 20 epoch不降即停止
- Batch size：64
- Epochs：最大200

### 5.5 参数平滑（实盘推理）

```python
# 防止参数日间剧烈跳变
smoothed_params = 0.7 * current_prediction + 0.3 * previous_params
```

---

## 6. 后端与部署 (Backend & Serving)

### 6.1 FastAPI 推理服务 (`fastapi_app.py`)

```
POST /predict
  Body: {"stock_code": "sh600036"}
  
  逻辑:
  1. 查询 TDengine: 最近240条1分钟线
  2. 特征工程: 26维特征 (使用 scaler.pkl 标准化)
  3. 模型推理: → (strategy_id, params)
  4. 参数平滑: EMA with 历史输出
  5. 返回: {"strategy_id": 2, "params": [3.0, 10, 2.5]}
```

### 6.2 容错机制
- 模型服务不可用时 → 返回默认保守参数
- TDengine 查询超时 → 使用缓存的最新特征
- 单只股票失败 → 不影响其他股票

### 6.3 SpringBoot 集成
- 定时拉取所有标的的推理结果（每分钟轮询）
- WebSocket 推送策略变更到前端
- MySQL 存储推理历史 `strategy_params` 表

### 6.4 前端可视化
- K线图上叠加"当前激活策略"类型标记
- 参数面板：显示5个策略的参数 + 当前选中策略高亮
- 历史参数变化折线图（可回溯策略切换点）
- 回测进度条

---

## 7. 实施步骤 (Action Plan v2)

### 第一阶段：数据管线 ✅ (完成)
- [x] **任务 1.1**：`data_fetcher.py` — zzshare 数据采集 + TDengine 存储 (分钟线+情绪)
- [x] **任务 1.2**：`stock_screener.py` — 筛选短线标的 → `stock_list_100.csv` + 80只云服务器运行中
- [x] **任务 1.3**：`feature_engineering.py` — 26维特征计算 + scaler
- [ ] **任务 1.4**：`fill_gaps.py` — 补缺漏交易日 (云服务器跑完后生成)

### 第二阶段：样本生产
- [ ] **任务 2.1**：`backtest_engine.py` — 5策略回测引擎 (T+1约束)
- [ ] **任务 2.2**：`sample_generator.py` — 滑动窗口(stride=60) + Optuna 50 trials + Plan B 负采样 → Parquet
- [ ] **任务 2.2**：`sample_generator.py` — 滑动窗口 + Optuna多策略寻优 → Parquet

### 第三阶段：模型训练
- [ ] **任务 3.1**：`dataset.py` — IterableDataset 读取 Parquet
- [ ] **任务 3.2**：`model.py` — LSTM多任务网络
- [ ] **任务 3.3**：`train.py` — 训练 + 早停 + 验证

### 第四阶段：服务集成
- [ ] **任务 4.1**：`fastapi_app.py` — 推理服务 + 参数平滑
- [ ] **任务 4.2**：SpringBoot 调用 + WebSocket + 前端联调

---

## 8. 技术栈清单

| 层级 | 技术/框架 | 版本/备注 |
|:---|:---|:---|
| 数据获取 | **zzshare** (自在量化) | 免费，60次/分钟，2005年至今分钟线 + 情绪数据 |
| 特征计算 | TA-Lib | `pip install TA-Lib` (C库需单独安装) 或 `ta` (纯Python) |
| 数据库 | TDengine 3.3.8 | Docker部署，taos-ws-py连接 |
| 回测 | VectorBT + 自定义 | 向量化日线回测，逐笔分钟线仿真 |
| 参数寻优 | Optuna | `pip install optuna` |
| 机器学习 | PyTorch 2.0+ | LSTM + MLP |
| 模型服务 | FastAPI | 异步，/predict 接口 |
| 后端 | SpringBoot 2.7+ | Java业务层 |
| 前端 | Vue3 + Vite + Electron + ECharts | 桌面端 |

---

## 9. 关键注意事项

1. **未来函数**：特征窗口和回测窗口必须时间分离。步长60条使特征窗口有重叠，但每个窗口的**回测窗口(标签)永不重叠**
2. **T+1 交易约束**：按真实交易日日期分组，同一交易日不可买+卖。买入在第 N 天 → 最早第 N+1 天卖出
3. **回测摩擦**：千1.5手续费 + 0.1%滑点 + 涨跌停检测（涨停买不进/跌停卖不出）
4. **停牌缺口**：`has_gap()` 检测时间序列 ≥ 2小时断裂，跳过该窗口
5. **时间序列验证**：按时间切分训练/验证集，严禁 shuffle
5. **API 速率控制**：zzshare 有 Token 下 60次/分钟，fetcher 内置令牌桶限流器
6. **参数平滑**：实盘输出做 EMA 平滑，避免策略频繁切换
7. **多策略协同**：相邻时间的策略切换应有冷却期（如至少持仓30分钟后才允许换策略）
8. **系统解耦**：Python模型服务挂了，Java返回默认策略+保守参数
9. **Plan B 负采样**：每窗口4条记录(1正+3负)，模型不仅学会"什么好"，也学会"什么差"

---

## 10. 项目文件结构

```text
nessaj-dynamic-quant-strategy/
├── project_prompt.md
├── backend_java/                    # SpringBoot 后端
│   ├── src/main/java/
│   └── pom.xml
├── python_core/                     # Python 核心
│   ├── config/
│   │   ├── config.yaml              # TDengine连接 + 采集参数
│   │   └── strategy_templates.yaml  # 策略模板定义
│   ├── src/
│   │   ├── data_fetcher.py          # 数据采集 (zzshare→TDengine, 含情绪)
│   │   ├── fill_gaps.py              # 补缺漏交易日
│   │   ├── utils.py                  # 共享工具 (代理清理)  
│   │   ├── stock_screener.py        # 选股筛选
│   │   ├── feature_engineering.py   # 26维特征工程
│   │   ├── backtest_engine.py       # 多策略回测引擎
│   │   ├── sample_generator.py      # Optuna样本生成
│   │   ├── dataset.py               # PyTorch数据集
│   │   ├── model.py                 # LSTM多任务网络
│   │   ├── train.py                 # 训练脚本
│   │   └── fastapi_app.py           # 推理服务
│   ├── data/
│   │   ├── stock_list_100.csv       # 100只短线标的
│   │   ├── train.parquet
│   │   └── logs/
│   ├── models/
│   │   ├── best_model.pth
│   │   └── scaler.pkl
│   └── requirements.txt
├── frontend_vue/                    # Vue3 前端
│   ├── src/
│   │   ├── components/
│   │   └── views/
│   └── package.json
└── README.md
```
