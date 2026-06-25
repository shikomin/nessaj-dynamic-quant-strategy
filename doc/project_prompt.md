# A股动态参数量化交易系统 - 项目开发文档 (Project Prompt)

> **修订版 v2.2** — 2026-06-25  
> Phase 01 完成：数据管线跑通，179只股1分钟线+26维特征入库  
> Phase 01 问题：Calmar>0 占比仅 1.8%，样本质量不足以支持 LSTM 训练  
> 本轮重构重点：多仓位回测引擎、复合策略信号、涨跌停按板块区分、单进程线程池架构

---

## 1. 项目概述 (Project Overview)

### 1.1 核心理念
本项目构建一个 **"多策略动态参数量化交易系统"** 。核心思想：

1. **不做价格预测** -- 不预测涨跌，而是预测"当前市场状态下，哪种策略 + 哪组参数最有效"
2. **多策略覆盖** -- 不同市况（趋势/震荡/高波/放量）用不同策略模板，模型自适应选择
3. **分钟级粒度** -- 用分钟线生成训练样本，数据量从 ~2000 条跃升到 40万+ 条

### 1.2 技术架构总览
```
数据采集(zzshare→TDengine) → 特征工程(大盘/板块/个股特征) → 样本生成(多仓位回测+Optuna→Parquet)
→ 模型训练(LSTM多任务:策略分类+参数回归) → FastAPI推理 → SpringBoot业务 → Vue前端
```

| 层级 | 技术 |
|:---|:---|
| 数据获取 | **zzshare** (自在量化) -- 免费，2005年至今分钟线，60次/分钟 |
| 数据库 | TDengine 3.3.8 (时序) + MySQL 8.0 (关系) |
| 特征工程 | Pandas (纯Python, 无TA-Lib依赖), 大盘/板块/个股多维特征 |
| 样本生成 | Optuna (每个策略独立 study, 10-15 trials) |
| 回测引擎 | 自定义多仓位向量化引擎 (固定资金50,000, 5仓位) |
| 模型 | PyTorch LSTM + MLP 多任务学习 |
| 模型服务 | FastAPI |
| 后端 | SpringBoot 2.7+ |
| 前端 | Vue3 + Vite + TypeScript + Electron + ECharts |

### 1.3 数据流向
```
1. 数据采集: zzshare → TDengine (个股分钟线 + 指数分钟线 + 情绪)
2. 定时积累: 每日运行 data_fetcher.py → TDengine (增量, 179只股/分钟)
3. 特征工程: TDengine → 个股特征 + 大盘特征 + 板块特征 → Parquet
4. 样本生成: Parquet → Optuna多策略回测(多仓位) → (features, strategy_label, params_label) → train.parquet
5. 模型训练: train.parquet → PyTorch LSTM → best_model.pth + scaler.pkl
6. 实盘推理: 实时行情 → FastAPI (加载模型) → 策略类型 + 参数向量
7. 参数平滑: 输出参数 → EMA平滑 → 交易执行
```

### 1.4 关键问题与 v2.2 解决方案

| # | 问题 | v2.1 状态 | v2.2 修复 |
|---|------|-----------|-----------|
| 1 | Calmar>0 占比仅 1.8% | T+1 + 2天窗口, 单笔交易 | **多仓位(5仓)** + 延长回测窗口(5-10天) |
| 2 | 回测不强制平仓后总资金计算 | 强制平仓 | **不强制平仓**: 窗口末按市值+现金计算总资产 |
| 3 | 涨跌停硬编码 10% | 所有股票统一 | **按板块区分**: 主板10%/创业板20%/科创板20%/北交所30% |
| 4 | 样本生成 2 worker 内存 2.7G/3.6G | ProcessPoolExecutor | **单进程 + 线程池**: 峰值 ~200MB, 4核4G安全 |
| 5 | Optuna 50 trials 混合搜索 5策略 | 每个策略仅 ~10 trials | **每策略独立 study**: 5×15 trials, 搜索质量提升 |
| 6 | `np.roll` 边界信号错误 | 循环移位 | **shift(1) 切片** |
| 7 | 特征缺乏市场水位 | 26维仅个股指标 | **+大盘涨跌幅 + 板块相对强度** |
| 8 | 策略信号过于简单 | 单条件触发 | **主信号 + 确认过滤** (量能确认/趋势过滤/涨停距过滤) |

---

## 2. 多策略模板设计 (Multi-Strategy Templates)

### 2.1 策略矩阵

| 策略ID | 名称 | 适用市况 | 主信号参数(p1,p2) | 风控参数(p3,p4,p5) |
|--------|------|---------|-------------------|-------------------|
| 0 | 双均线突破 (MA Breakout) | 单边趋势 | fast_period(2-20), slow_period(20-120) | stop_atr, profit_pct, max_hold |
| 1 | 布林带回归 (BB Reversal) | 震荡市 | bb_period(10-50), bb_std(1.5-3.0) | stop_atr, profit_pct, max_hold |
| 2 | 放量突破 (Vol Breakout) | 行情启动 | vol_ratio(1.5-5.0), lookback(5-30) | stop_atr, profit_pct, max_hold |
| 3 | ATR通道突破 (ATR Channel) | 高波动 | atr_period(5-30), atr_mult(1.5-4.0) | stop_atr, profit_pct, max_hold |
| 4 | 动量突破 (Momentum) | 强势行情 | mom_period(5-30), mom_thresh(0.02-0.10) | stop_atr, profit_pct, max_hold |

### 2.2 复合策略信号 (v2.2 新增)

每个策略的**主信号**需额外通过以下**确认过滤条件**才触发开仓：

| 过滤条件 | 参数 | 作用 |
|---------|------|------|
| 量能确认 | vol_confirm (成交量 > N日均量) | 防无量假突破 |
| 趋势过滤 | trend_filter (价格 > MA60) | 只在多头趋势中操作 |
| 涨停距过滤 | limit_distance (距涨停线 > N%) | 避免涨停板附近追高 |
| 波动率过滤 | vol_filter (ATR/close < N%) | 避免极端波动市 |

每个过滤条件是否启用 + 参数由 Optuna 搜索决定。组合空间：5策略 × 2^4过滤组合 = 80个基础模板。

**v2.2 新增通用参数**（所有策略共享，与 p1-p5 一起被 Optuna 搜索）：

| 参数 | 范围 | 说明 |
|------|------|------|
| buy_ratio | 0.2 ~ 1.0 | 每次买入占可用现金的比例上限 |
| sell_ratio | 0.3 ~ 1.0 | 每次卖出占当前持股的比例上限 |
| cooling | 5 ~ 120 | 同策略冷却期分钟数，防止信号频繁触发 |

**v2.2 回测模拟项更新**：万一佣金(0.001) + 0.05%滑点 + 千一印花税(卖出单向) + 涨跌停按板块区分(主板10%/创业板20%/科创板20%/北交所30%/ST股5%) + 一字板检测 + 停牌缺口跳过

### 2.3 参数向量定义

```python
# 每个策略 5 个专属参数 + 3 个通用参数
STRATEGY_PARAMS = {
    0: {"p1_name": "fast_ma", "p1_range": (2, 20),
        "p2_name": "slow_ma", "p2_range": (20, 120),
        "p3_name": "stop_atr", "p3_range": (1.0, 4.0),
        "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
        "p5_name": "max_hold", "p5_range": (60, 480)},
    # ... 其他策略类似
}

# v2.2 新增：通用仓位管理参数 (所有策略共享)
COMMON_PARAMS = {
    "buy_ratio": (0.2, 1.0),      # 买入占可用现金比例
    "sell_ratio": (0.3, 1.0),     # 卖出占当前持股比例
    "cooling": (5, 120),           # 同策略冷却期(分钟)
}
```

### 2.4 涨跌停按板块区分 (v2.2 新增)

```python
def get_limit_pct(stock_code: str) -> float:
    """
    sh60xxxx  → 主板 ±10%
    sz00xxxx  → 主板 ±10%
    sz30xxxx  → 创业板 ±20%
    sh688xxx  → 科创板 ±20%
    sz00xxxx 中以 002 开头 → 中小板 ±10% (部分已改为20%，需确认)
    bjxxxxxx  → 北交所 ±30%
    """
```

### 2.5 回测摩擦成本

| 成本项 | 费率 | 说明 |
|--------|------|------|
| 佣金 (买入+卖出) | 0.001 (万一, 降为实际水平) | v2.2 从千1.5下调 |
| 滑点 (买入+卖出) | 0.0005 (0.05%) | v2.2 从 0.1% 下调 |
| 印花税 | 0.001 (卖出单向) | A股卖出收千一 |
| 涨跌停限制 | 按板块区分 | 涨停买不进 / 跌停卖不出 |

---

## 3. 数据处理与特征工程 (Data & Features)

### 3.1 数据采集策略

| 数据类型 | 频率 | 来源 | 说明 |
|---------|------|------|------|
| 个股1分钟K线 | 首次全量 + 每日增量 | zzshare | 179只股 |
| 指数1分钟K线 | 首次全量 + 每日增量 | zzshare | 上证指数/深证成指/创业板指 |
| 个股日线K线 | 首次全量 + 每日增量 | zzshare | 辅助指标 |
| 市场情绪K线 | 每日增量 | zzshare | 市场整体情绪 |

**代码格式映射**：
- 内部: `sh600036` / `sz300774`
- zzshare: `600036.SH` / `300774.SZ`

### 3.2 特征工程 (v2.2 扩展)

#### 3.2.1 个股特征 (26维，保持现有)

| 类别 | 特征 | 维度 |
|------|------|------|
| 价格基础 | open, high, low, close | 4 |
| 成交量 | volume, volume_ma5, volume_ratio | 3 |
| 趋势类 | ma5, ma10, ma20, ma60 | 4 |
| 动量类 | rsi_6, rsi_14, macd, macd_signal, macd_hist | 5 |
| 波动类 | atr_14, bb_upper, bb_lower, bb_width | 4 |
| 量价类 | obv, mfi_14 | 2 |
| 市场结构 | amplitude_pct, volume_momentum, price_position | 3 |
| 情绪 | sentiment | 1 |

#### 3.2.2 大盘/板块特征 (v2.2 新增 ~5维)

| 特征 | 来源 | 说明 |
|------|------|------|
| index_change_pct | 对应指数1分钟线 | 大盘涨跌幅 (上证/深证/创业板) |
| sector_rank | 板块行情API | 所属板块当日强度排名 |
| relative_strength | 计算 | (个股涨幅 - 指数涨幅), 相对强弱 |
| market_breadth | 指数内部 | 涨跌家数比 (如有数据源) |

#### 3.2.3 标准化
- 训练集拟合 `RobustScaler`（抗异常值），保存为 `models/scaler.pkl`
- 推理时加载同一 scaler 做变换
- 大盘/板块特征与个股特征统一 scaler

### 3.3 交易日处理

zzshare 的 `stk_mins()` 只返回实际交易分钟的数据（9:30-11:30, 13:00-15:00），周末/节假日/午休不产生数据行。特征工程无需额外过滤非交易时间。技术指标跨交易日连续计算是预期行为（周五MA延续到下周一）。

---

## 4. 训练样本生成 (Sample Generation)

### 4.1 滑动窗口设计

```
                    时间轴 ->
|------ 特征窗口(1天) ------|------ 回测窗口(5-10天) ------|
         240条1分钟线                  1200-2400条1分钟线

步长 = 240条 (1天)，相邻窗口特征不重叠 (v2.2 从 60 改为 240)
```

| 参数 | 值 | 说明 |
|------|-----|------|
| 特征窗口 | 240条 (1个交易日) | 个股+大盘/板块特征, 作为模型输入 |
| **回测窗口** | **1200-2400条 (5-10天)** | v2.2 从2天延长, 提供足够交易机会 |
| **步长** | **240条 (1天)** | v2.2 从60改为240, 减少冗余样本 |
| 数据量/股 | ~200个窗口 (1年分钟线) | 按天而非按小时滑动 |

### 4.2 多批次资金池回测引擎 (v2.2 重写)

```
                    单资金池: 50,000 元
 ┌──────────┐  ┌──────────────────────────────────┐
 │   现金    │  │     持仓批次 (FIFO 管理)           │
 │ 动态变化  │  │                                    │
 │          │  │  批次1: D1 10手@10.50, T+1→D2可卖  │
 │   ───────│  │  批次2: D2 5手@10.80,  T+1→D3可卖  │
 │  买入←───│──│  批次3: D3 8手@10.30,  T+1→D4可卖  │
 │  ───────→│  │  批次4: D4 3手@11.20,  T+1→D5可卖  │
 │  卖出     │  │                                    │
 └──────────┘  └──────────────────────────────────┘

窗口结束时:
  总资产 = 现金 + Σ(各批次持股数 × 当前收盘价)
  总收益 = (总资产 - 50,000) / 50,000
  **不强制平仓**: 未平仓批次按当前市价计入总资产
```

**批次管理规则**：
1. 单资金池: 初始 50,000 元，买入时占用现金，卖出时释放回现金
2. 买入比例 buy_ratio: 策略参数之一（Optuna 搜索），决定每次买入占可用现金的百分比上限
3. 卖出比例 sell_ratio: 策略参数之一（Optuna 搜索），决定每次卖出占当前持股的百分比上限
4. T+1 按批次独立: 每个买入批次记录自己的 entry_date_idx，卖出时只能选择 T+1 已满足的批次
5. FIFO 卖出: 多个可卖批次时，优先卖出最早的批次；部分卖出时拆分批次
6. 同分钟信号优先级: 风控信号(止盈/止损/超时) > 卖出信号 > 买入信号
7. 冷却期: 同一策略方向，上次开仓后至少间隔 N 分钟才允许再次开仓（Optuna 搜索参数）
8. 资金约束: 买入前检查 `(现金 × buy_ratio) / (股价 × 100)` 是否 ≥ 1 手

**绩效计算方式改变**：
- 不再按"交易序列"计算 Calmar，而是模拟每日净值曲线：每根 K 线计算 `总资产 = 现金 + 持仓市值`
- 从 2400 根 K 线的净值曲线计算 max_dd → Calmar
- 优点：更真实反映资金曲线的波动，5 天窗口可产生 10-20 笔交易，Calmar 更稳定

### 4.3 Optuna 搜索 (v2.2 改进)

```python
# 每个窗口 = 对每个策略独立 study (不再混合搜索)
for strategy_id in range(5):
    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda trial: _objective_strategy(trial, bt_df, strategy_id),
        n_trials=15  # v2.2 从 50 降低, 但每个策略都能得到充分搜索
    )
    # 记录该策略在此窗口的最佳表现

# 搜索空间: 策略专属 p1-p5 + 通用 buy_ratio/sell_ratio/cooling + 过滤条件
# 每个 trial: 随机采样参数 → 多批次回测 → 返回净值曲线 Calmar

# 选择表现最好的 N 个策略组合作为正样本候选
# 阈值: Calmar > 0.5, trades >= 3
```

### 4.4 Plan B 负采样 (保持 v2.1 设计)

每个窗口生成 **4条训练记录**：

| 记录 | 来源 | 权重 |
|------|------|------|
| 正样本 ×1 | Calmar 最高且 > 阈值 的 trial | 1.0 |
| 差负样本 ×1 | Calmar 最低的 trial | 0.1 |
| 中负样本 ×2 | 随机中等 Calmar trials | 0.3 |

### 4.5 样本质量评估 (v2.2 新增)

生成完成后自动输出：

```python
总样本数: N
策略分布: {0: 18%, 1: 22%, 2: 25%, 3: 20%, 4: 15%}
Calmar 分布: >0: X% | >1: Y% | 中位数: Z
正样本 Calmar 均值: M
差负样本 Calmar 均值: N_diff
区分度 (正-负 Calmar 均值差): D
样本覆盖天数: covered / total_trading_days

收敛判断:
  - Calmar>0 占比 > 15%
  - 策略分布偏差 < 15%
  - 正负样本区分度 > 1.0
```

### 4.6 输出格式
- 文件：`data/train.parquet`, `data/val.parquet`
- 按时间切分：前80%训练，后20%验证（严禁随机打乱）
- 所有浮点字段为 float32
- features_blob: 改为共享索引引用 (v2.2, 训练时按索引取特征, 不再重复存储)

---

## 5. 样本生成架构 (v2.2 重写)

### 5.1 单进程 + 线程池 + 连接池

```
主进程:
  ├── TDengine 连接池 (2-3 连接, queue.Queue 线程安全)
  ├── 逐只股票串行处理 (每次只加载 1 只股票 ≈ 10MB)
  │   └── 对每个窗口:
  │       ├── ThreadPoolExecutor(max_workers=5) 并行运行 5 策略的 Optuna
  │       │   (Optuna 内部 numpy 操作释放 GIL, 线程有效)
  │       └── 收集结果 -> 写入临时文件
  └── 单只完成 -> 释放内存 -> 下一只
```

### 5.2 性能预估 (4核4G)

| 指标 | 值 |
|------|-----|
| 单只股票内存峰值 | ~200MB (原始数据 + Optuna studies + 样本缓冲) |
| CPU 利用率 | 50-70% (线程池 + GIL 释放) |
| 单窗口耗时 | ~0.5s (5策略 × 15 trials × 5ms/回测) |
| 179只 × 200窗口 | ~5 小时 |
| 崩溃恢复 | 单只完成后写 .parquet 标记, 断点续跑 |

### 5.3 日志规范

| 级别 | 内容 | 频率 |
|------|------|------|
| INFO | 每只股票开始/结束、总样本/跳过/Calmar统计 | 每只 3-5 条 |
| INFO | 每 50 窗口统计: 平均Calmar, 正样本占比 | 每 50 窗口 |
| DEBUG | 每条样本详情 (--verbose 开启) | 可选 |
| ERROR | 连接失败、回测异常 | 异常时 |

---

## 6. 模型设计 (Model Architecture)

### 6.1 多任务 LSTM 网络

```
输入: (Batch, 240, 26+~5=31维)

LSTM(hidden=128, layers=2, dropout=0.3, bidirectional=False)  # v2.2 去双向,防止信息泄露

  [取最后时间步]
Linear(128 → 128) + LayerNorm + ReLU + Dropout(0.3)

  ├─── 策略分类头 → Linear(128 → 64) → ReLU → Linear(64 → 5) + Softmax
  │    CrossEntropyLoss

  └─── 参数回归头 → Linear(128 → 64) → ReLU → Linear(64 → 5) + Sigmoid
       SmoothL1Loss (Huber, beta=0.5)
```

### 6.2 损失函数

```python
def joint_loss(strategy_logits, strategy_label, param_pred, param_label,
               calmar_weight, sample_weight):
    # 策略分类：加权交叉熵
    ce = F.cross_entropy(strategy_logits, strategy_label, reduction='none')
    ce = (ce * sample_weight).mean()

    # 参数回归：Huber Loss
    huber = F.smooth_l1_loss(param_pred, param_label, beta=0.5, reduction='none')
    huber = (huber.mean(dim=1) * calmar_weight * sample_weight).mean()

    return ce + 0.5 * huber
```

### 6.3 训练配置
- 优化器：AdamW (lr=1e-3, weight_decay=1e-4)
- 学习率调度：ReduceLROnPlateau (patience=10, factor=0.5)
- 早停：验证Loss 20 epoch不降即停止
- Batch size：64
- Epochs：最大200

---

## 7. 关键注意事项 (v2.2 更新)

1. **未来函数**：特征窗口和回测窗口必须时间分离。步长240条(1天)使特征窗口不重叠
2. **T+1 交易约束**：按交易日分组，每仓位独立记买入日期
3. **回测摩擦**：万一佣金(0.001) + 0.05%滑点 + 千一印花税(卖出单向) + 涨跌停按板块区分
4. **停牌缺口**：`has_gap()` 检测时间序列 >72h 断裂，跳过该窗口
5. **时间序列验证**：按时间切分训练/验证集，严禁 shuffle
6. **参数平滑**：实盘输出做 EMA 平滑，避免策略频繁切换
7. **多仓位风控**：最大持仓5个, 单仓位回撤 >20% 强制平仓
8. **系统解耦**：Python模型服务挂了，Java返回默认策略+保守参数
9. **Plan B 负采样**：每窗口4条记录(1正+3负)，模型学会相对排序
10. **大盘特征**：指数分钟线需与个股分钟线时间对齐，避免 look-ahead bias
11. **连接池**：TDengine 连接复用，避免频繁建连/断连
12. **features_blob 去重**：同窗口4条样本共享特征索引，训练时按索引取，减少75%存储

---

## 8. 实施步骤 (Action Plan v3)

### 第一阶段：数据管线 (重构)
- [ ] **任务 1.1**：`data_fetcher.py` — zzshare 数据采集（个股 + 指数 + 情绪），连接池化
- [ ] **任务 1.2**：`fill_gaps.py` — 数据完整性检查 + 批量补缺
- [ ] **任务 1.3**：`feature_engineering.py` — 个股26维 + 大盘/板块特征，连接池化

### 第二阶段：样本生产 (重写)
- [ ] **任务 2.1**：`backtest_engine.py` — 多仓位回测引擎 + 按板块涨跌停 + 复合策略信号
- [ ] **任务 2.2**：`sample_generator.py` — 单进程+线程池 + 每策略独立 Optuna + Plan B
- [ ] **任务 2.3**：`merge_samples.py` — 合并 + 时序排序 + 训练/验证切分 + 质量评估报告

### 第三阶段：模型训练
- [ ] **任务 3.1**：`dataset.py` — IterableDataset 读取 Parquet (按 features_blob 索引)
- [ ] **任务 3.2**：`model.py` — LSTM多任务网络
- [ ] **任务 3.3**：`train.py` — 训练 + 早停 + 验证

### 第四阶段：服务集成
- [ ] **任务 4.1**：`fastapi_app.py` — 推理服务 + 参数平滑
- [ ] **任务 4.2**：SpringBoot 调用 + WebSocket + 前端联调

---

## 9. 项目文件结构 (v2.2)

```text
nessaj-dynamic-quant-strategy/
├── project_prompt.md                  # 本文件
├── optimization_plan.md              # 详细优化分析
├── phase_01.md                       # Phase 01 总结
├── readme.md
├── backend_java/                     # SpringBoot 后端 (待开发)
├── python_core/
│   ├── config/
│   │   ├── config.yaml               # TDengine连接 + 采集参数 + 回测参数
│   │   └── config.example.yaml
│   ├── src/
│   │   ├── data_fetcher.py           # 数据采集 (个股+指数+情绪)
│   │   ├── fill_gaps.py              # 数据完整性检查
│   │   ├── feature_engineering.py    # 特征工程 (个股+大盘+板块)
│   │   ├── backtest_engine.py        # 多仓位回测引擎
│   │   ├── sample_generator.py       # 样本生成 (线程池+Optuna)
│   │   ├── merge_samples.py          # 合并+切分+质量评估
│   │   ├── dataset.py                # PyTorch数据集
│   │   ├── model.py                  # LSTM多任务网络
│   │   ├── train.py                  # 训练脚本
│   │   ├── fastapi_app.py            # 推理服务
│   │   ├── td_connector.py           # TDengine连接器 (含连接池)
│   │   ├── config.py                 # 配置加载
│   │   ├── logger.py                 # 日志配置
│   │   ├── rate_limiter.py           # API速率限制
│   │   ├── stock_screener.py         # 选股筛选
│   │   └── utils.py                  # 共享工具
│   ├── data/
│   │   ├── samples/                  # 各股票分样本文件
│   │   ├── train.parquet
│   │   ├── val.parquet
│   │   └── logs/
│   ├── models/
│   │   ├── best_model.pth
│   │   └── scaler.pkl
│   └── requirements.txt
└── frontend_vue/                     # Vue3 前端 (待开发)
```
