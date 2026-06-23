# Phase 01 总结 — 基础设施与数据管线

> **日期**: 2026-06-22  
> **目标**: 完成数据采集、存储、特征工程整条基础管线  
> **成果**: 100只A股短线标的的1分钟K线数据 + 26维特征入库，可进行后续训练

---

## 1. 完成清单

### 1.1 TDengine 时序数据库

| 超级表 | 用途 | 子表数 | 列数 |
|--------|------|--------|------|
| `kline_daily` | 日线K线 | 100 (`d_{code}`) | 7 (ts+OHLCV+amount) |
| `kline_1m` | 1分钟K线 | 100 (`m_{code}`) | 7 |
| `features_1m` | 26维分钟特征 | 100 (`f_{code}`) | 26 |
| `market_sentiment` | 市场情绪日K | 3 (sent_daily/hot/trend) | 7 |

### 1.2 Python 脚本

| 脚本 | 功能 | 关键设计 |
|------|------|---------|
| `data_fetcher.py` | zzshare → TDengine | 速率控制 60次/分钟、10天批写入、增量去重、代码映射(sh600036↔600036.SH) |
| `feature_engineering.py` | 原始K线 → 26维特征 | 纯 Pandas/NumPy、无 TA-Lib 依赖、市场情绪广播注入、增量计算 |
| `stock_screener.py` | 短线股筛选 | AkShare 实时行情筛选(换手/振幅/成交量) |
| `utils.py` | 代理清理 | `disable_system_proxy()` 公共函数 |

### 1.3 数据源切换

```
AkShare (5天) → 放弃
  ↓
zzshare (自在量化) → 免费，2005年至今，60次/分钟
  ↓
QMT/券商SDK → 备选，实盘+历史数据
```

### 1.4 股票池

| 阶段 | 数量 | 状态 |
|------|------|------|
| 初期测试 | 6只 (stock_list.csv 旧) | 已完成采集+特征 |
| 首次批量 | 20只 (手动精选) | 已完成采集+特征，存于 TDengine |
| 云服务器 | 80只 (从8296只中筛选) | 正在采集 |
| **最终** | **100只** (20旧+80新) | 80只跑完后即达成 |

### 1.5 核心参数

| 参数 | 值 | 说明 |
|------|-----|------|
| API速率 | 60次/分钟 | zzshare Token |
| 日历回溯 | 370天 | 实际约252交易日 |
| 批写入大小 | 10天/批 | 2400条/批 |
| 特征维度 | 26维 | 25技术指标+1市场情绪 |
| 股票数据量 | ~480万条/100股 | 252天×240条/天×100股 |

---

## 2. 设计演进（偏离原 project_prompt 的点）

### 2.1 数据源

| 原方案 | 最终方案 | 原因 |
|--------|---------|------|
| AkShare 每日积累 | **zzshare 一次性全量** | AkShare 仅5天分钟线，zzshare 有20年+历史 |

### 2.2 策略模板

| 原方案 | 最终方案 |
|--------|---------|
| 单一双均线(3参数) | **5策略**：MA突破/布林回归/放量突破/ATR通道/动量突破，每策略3参数 |
| 固定百分比止损 | **ATR倍数止损**（自适应波动率） |

### 2.3 样本生成

| 原方案 | 最终方案 (Plan B) |
|--------|------------------|
| 每个窗口1条(最优解) | 每个窗口**4条**(1正样本×1.0 + 3负样本加权) |
| 简单加权MSE | **Pairwise Ranking Loss**，模型学会远离差策略 |
| 步长240条(1天) | **步长60条(1小时)**，样本量×4 |

### 2.4 特征工程

| 原方案 | 最终方案 |
|--------|---------|
| 25维 | **26维**（+1市场情绪） |
| TA-Lib 依赖 | **纯 Pandas/NumPy**（跨平台无C库依赖） |

### 2.5 T+1 交易约束

原方案未明确处理 → 最终方案在 `backtest_engine.py` 中显式实现 T+1（当日买入最早次日卖出）。

### 2.6 数据补缺

新增 `fill_gaps.py` 脚本，从 TDengine 反查缺失交易日并补拉（待跑完后生成）。

---

## 3. 平台兼容修复

| 问题 | 平台 | 修复 | 影响文件 |
|------|------|------|---------|
| `datetime` vs `datetime64` 比较失败 | Linux (pandas ≥2.0) | `pd.Timestamp` + `tz_localize(None)` | data_fetcher.py, feature_engineering.py |
| `taos-ws-py` `fetch_all()` 不存在 | 0.6.9 | `list(result)` 替代 | data_fetcher.py, feature_engineering.py |
| `MAX(ts)` 参数错误 | 0.6.9 | `ORDER BY ts DESC LIMIT 1` 替代 | data_fetcher.py, feature_engineering.py |
| 系统代理导致 HTTP 请求失败 | Windows | `disable_system_proxy()` | utils.py |
| 东方财富限流 | Windows | zzshare 替代 AkShare | data_fetcher.py |

---

## 4. 文件变更清单

```
新增:
  python_core/config/config.yaml          # TDengine + zzshare 配置
  python_core/requirements.txt            # zzshare, taos-ws-py, pandas, pyyaml
  python_core/src/data_fetcher.py         # 主数据采集 (zzshare)
  python_core/src/feature_engineering.py  # 26维特征计算
  python_core/src/stock_screener.py       # 选股器
  python_core/src/utils.py                # 共享工具
  python_core/stock_list.csv              # 80只短线标的 (云服务器运行中)
  python_core/data/stock_list_100.csv     # 人工精选100只
  python_core/data/logs/                  # 运行日志

修改:
  project_prompt.md                       # 同步设计变更
  readme.md                               # 架构图

DDL (手动执行):
  quant_dynamic 数据库
  kline_daily / kline_1m / features_1m / market_sentiment 超级表
  200+ 子表 (代码自动创建)
```

---

## 5. 待完成

| 优先级 | 任务 | 脚本 |
|--------|------|------|
| P0 | 云服务器 80只股数据采集 | `data_fetcher.py` (运行中) |
| P1 | 补缺漏的交易日 | `fill_gaps.py` (待写) |
| P1 | 5策略回测引擎 + T+1 | `backtest_engine.py` |
| P1 | Optuna 样本生成 + Plan B 负采样 | `sample_generator.py` |
| P2 | LSTM 多任务模型 | `model.py` |
| P2 | 训练脚本 | `train.py` |
| P3 | FastAPI 推理服务 | `fastapi_app.py` |
