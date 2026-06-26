#!/usr/bin/env python3
"""
多策略回测引擎 v3.1
====================

v3.1 核心改动:
  1. 9 策略 — 5趋势 + 2反脆弱 + 2行为金融
  2. 去掉 MACD/KDJ 硬门槛 — 让数据决定何时触发
  3. 多批次资金池 — FIFO, 最多10批次, 各批次独立T+1
  4. 涨跌停按板块逐日更新, ATR(480), 净值曲线 Calmar

===================================================================
策略矩阵
---------
趋势类 (0-4):    MA突破 / 布林回归 / 放量突破 / ATR通道 / 动量突破
反脆弱类 (5-6):  跌停次日跳空 / 缩量阴跌末端
行为金融类 (7-8): 突破回踩确认 / V型反转

===================================================================
参数体系
---------
策略专属(2):  p1, p2 (Optuna/随机搜索)
通用参数(6):  stop_atr, profit_pct, max_hold, buy_ratio, sell_ratio, cooling
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass

# ============================================================
# 成本常量
# ============================================================
COMMISSION = 0.0001      # 万1 佣金 (买入单向)
SLIPPAGE  = 0.0005      # 0.05% 滑点
STAMP_TAX = 0.001       # 千1 印花税 (卖出单向)

# ============================================================
# 资金池超参数
# ============================================================
CAPITAL  = 50000.0
MAX_LOTS = 10

# ============================================================
# 窗口超参数
# ============================================================
BACKTEST_WARMUP = 1200
BACKTEST_WINDOW = 1200

# ============================================================
# 通用参数搜索空间
# ============================================================
COMMON_PARAMS = {
    "stop_atr":   (1.0, 4.0),
    "profit_pct": (0.01, 0.10),
    "max_hold":   (60, 480),
    "buy_ratio":  (0.2, 1.0),
    "sell_ratio": (0.3, 1.0),
    "cooling":    (5, 120),
}

# ============================================================
# 9 策略定义
# ============================================================
STRATEGY_PARAMS = {
    # ── 趋势类 ──
    0: {"name": "MA突破",    "p1_name": "fast_ma",    "p1_range": (2, 20),
        "p2_name": "slow_ma",     "p2_range": (20, 120)},
    1: {"name": "布林回归",  "p1_name": "bb_period",  "p1_range": (10, 50),
        "p2_name": "bb_std",      "p2_range": (1.5, 3.0)},
    2: {"name": "放量突破",  "p1_name": "vol_ratio",  "p1_range": (1.5, 5.0),
        "p2_name": "lookback",    "p2_range": (5, 30)},
    3: {"name": "ATR通道",   "p1_name": "atr_period", "p1_range": (5, 30),
        "p2_name": "atr_mult",    "p2_range": (1.5, 4.0)},
    4: {"name": "动量突破",  "p1_name": "mom_period", "p1_range": (5, 30),
        "p2_name": "mom_thresh",  "p2_range": (0.02, 0.10)},

    # ── 反脆弱类 ──
    5: {"name": "跌停次日跳空", "p1_name": "vol_shrink",  "p1_range": (0.5, 0.9),
        "p2_name": "gap_pct",      "p2_range": (0.01, 0.05)},
    6: {"name": "缩量阴跌末端", "p1_name": "decline_bars","p1_range": (3, 8),
        "p2_name": "vol_decay",    "p2_range": (0.15, 0.30)},

    # ── 行为金融类 ──
    7: {"name": "突破回踩确认", "p1_name": "break_period","p1_range": (10, 30),
        "p2_name": "pullback_pct", "p2_range": (0.01, 0.05)},
    8: {"name": "V型反转",    "p1_name": "amp_thresh",  "p1_range": (3, 8),
        "p2_name": "close_pct",    "p2_range": (0.3, 0.7)},
}


# ============================================================
# 涨跌停按板块
# ============================================================

def get_limit_pct(stock_code: str) -> float:
    code = stock_code.lower()
    if code.startswith('sh688'): return 0.20
    if code.startswith('sz30'):  return 0.20
    if code.startswith('bj'):    return 0.30
    return 0.10


# ============================================================
# 缺口检测
# ============================================================

def has_gap(df: pd.DataFrame, max_gap_hours: int = 96) -> bool:
    if 'ts' not in df.columns or len(df) < 2:
        return False
    gaps = df['ts'].diff().dropna()
    return (gaps.dt.total_seconds() / 3600 > max_gap_hours).any()


# ============================================================
# 持仓批次
# ============================================================

@dataclass
class Lot:
    entry_bar: int
    entry_date_idx: int
    shares: int
    entry_price: float
    highest_close: float


# ============================================================
# 主入口: 单策略回测
# ============================================================

def run_backtest(df: pd.DataFrame, strategy_id: int,
                 p1: float, p2: float,
                 stop_atr: float = 2.0, profit_pct: float = 0.05,
                 max_hold: int = 240,
                 buy_ratio: float = 0.5, sell_ratio: float = 0.5,
                 cooling: int = 30,
                 stock_code: str = "",
                 capital: float = CAPITAL,
                 commission: float = COMMISSION,
                 slippage: float = SLIPPAGE) -> dict:
    """
    单策略回测。sample_generator 每次 trial 调用此函数。
    """
    n = len(df)
    if n < BACKTEST_WARMUP + 60:
        return _empty()

    ts     = pd.to_datetime(df['ts'])
    close  = df['close'].values.astype(float)
    high   = df['high'].values.astype(float)
    low    = df['low'].values.astype(float)
    open_  = df['open'].values.astype(float)
    volume = df['volume'].values.astype(float)

    warmup_end = min(BACKTEST_WARMUP, n)
    trade_start = warmup_end
    if trade_start >= n:
        return _empty()

    limit_pct = get_limit_pct(stock_code)
    limit_map = _build_daily_limits(close, ts, warmup_end, limit_pct)

    atr = _calc_atr(high, low, close, 480)

    # v3.1: 不加 MACD/KDJ 硬门槛, 信号直接用于交易
    buy_signal = _gen_buy_signal(strategy_id, close, high, low, volume, p1, p2)

    dates = ts.dt.date
    unique_dates = sorted(dates.unique())

    eq_curve = _simulate_multi_lot(
        open_, high, low, close, atr, ts, unique_dates,
        buy_signal, trade_start,
        stop_atr, profit_pct, max_hold,
        buy_ratio, sell_ratio, cooling, capital,
        limit_map, commission, slippage,
    )

    return _calc_metrics_from_curve(eq_curve, capital)


# ============================================================
# 买入信号生成 (9 策略)
# ============================================================

def _gen_buy_signal(sid: int, close: np.ndarray, high: np.ndarray,
                    low: np.ndarray, volume: np.ndarray,
                    p1: float, p2: float) -> np.ndarray:
    """生成布尔数组: True = 该K线有该策略的买入信号。"""
    n = len(close)

    # ── 趋势类 ──
    if sid == 0:
        f, s = int(p1), int(p2)
        if f >= s or s >= n: return np.zeros(n, dtype=bool)
        mf = pd.Series(close).rolling(f, min_periods=1).mean().values
        ms = pd.Series(close).rolling(s, min_periods=1).mean().values
        pmf = np.empty_like(mf); pmf[0]=mf[0]; pmf[1:]=mf[:-1]
        pms = np.empty_like(ms); pms[0]=ms[0]; pms[1:]=ms[:-1]
        return (mf > ms) & (pmf <= pms)

    elif sid == 1:
        p = int(p1)
        if p >= n: return np.zeros(n, dtype=bool)
        mid = pd.Series(close).rolling(p, min_periods=1).mean().values
        std = pd.Series(close).rolling(p, min_periods=1).std().values
        lower = mid - p2 * std
        return (close <= lower) & (close > np.roll(close, 1))

    elif sid == 2:
        lb = int(p2)
        if lb >= n: return np.zeros(n, dtype=bool)
        vma = pd.Series(volume).rolling(lb, min_periods=1).mean().values
        hh  = pd.Series(high).rolling(lb, min_periods=1).max().values
        phh = np.empty_like(hh); phh[0]=hh[0]; phh[1:]=hh[:-1]
        return (volume > vma * p1) & (close > phh)

    elif sid == 3:
        ap = int(p1)
        if ap >= n: return np.zeros(n, dtype=bool)
        ma = pd.Series(close).rolling(ap, min_periods=1).mean().values
        atr_v = _calc_atr(high, low, close, ap)
        return close > (ma + p2 * atr_v)

    elif sid == 4:
        mp = int(p1)
        if mp >= n: return np.zeros(n, dtype=bool)
        mom = (close - np.roll(close, mp)) / (np.roll(close, mp) + 1e-9)
        vma = pd.Series(volume).rolling(mp, min_periods=1).mean().values
        pvma = np.empty_like(vma); pvma[0]=vma[0]; pvma[1:]=vma[:-1]
        return (mom > p2) & (volume > pvma)

    # ── 反脆弱类 ──
    elif sid == 5:
        # 跌停次日跳空: 昨日跌停(收盘=-10%) + 量缩到均量的p1以下 → 今日低开>p2%买入
        limit_pct = 0.10
        pre_close = np.roll(close, 1)
        pre_close[0] = close[0]
        was_limit_down = (pre_close <= np.roll(close, 2) * (1 - limit_pct))
        vol_ma20 = pd.Series(volume).rolling(20, min_periods=1).mean().values
        vol_shrunk = volume < vol_ma20 * p1
        gap_down = open_ < pre_close * (1 - p2)
        return was_limit_down & vol_shrunk & gap_down

    elif sid == 6:
        # 缩量阴跌末端: 连跌 p1 根K线 + 每根量比前一根缩 p2
        decline = close < np.roll(close, 1)
        consecutive = pd.Series(decline).rolling(int(p1), min_periods=int(p1)).sum().values >= int(p1)
        vol_ratio = volume / (np.roll(volume, 1) + 1e-9)
        vol_declining = vol_ratio < (1 - p2)
        return consecutive & vol_declining

    # ── 行为金融类 ──
    elif sid == 7:
        # 突破回踩: 突破p1日高点后, 回踩到高点×(1-p2)以内 + 收涨确认
        bp = int(p1)
        if bp >= n: return np.zeros(n, dtype=bool)
        hh_p = pd.Series(high).rolling(bp, min_periods=1).max().values
        broke_out = close > np.roll(hh_p, 1)
        pullback = close > hh_p * (1 - p2)
        rising = close > np.roll(close, 1)
        return broke_out & pullback & rising

    elif sid == 8:
        # V型反转: 单根振幅>p1% + 收在极端位±p2%
        amp = (high - low) / (np.roll(close, 1) + 1e-9) * 100
        close_pos = (close - low) / (high - low + 1e-9)
        high_amp = amp > p1
        at_top = close_pos > p2
        at_bot = close_pos < (1 - p2)
        return high_amp & (at_top | at_bot)

    return np.zeros(n, dtype=bool)


# ============================================================
# ATR / 每日涨跌停
# ============================================================

def _calc_atr(high, low, close, period):
    pc = np.roll(close, 1); pc[0] = pc[0]
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    tr[0] = high[0] - low[0]
    return pd.Series(tr).ewm(alpha=1/period, min_periods=period).mean().values


def _build_daily_limits(close, ts, warmup_end, limit_pct):
    dates = ts.dt.date
    all_bars_date = dates.values if hasattr(dates, 'values') else dates
    daily_last_close = {}
    for i in range(len(close)):
        d = all_bars_date[i] if hasattr(all_bars_date[i], 'strftime') else all_bars_date[i]
        daily_last_close[d] = close[i]
    sorted_dates = sorted(daily_last_close.keys())
    limit_map = {}
    for idx, d in enumerate(sorted_dates):
        prev = close[0] if idx == 0 else daily_last_close[sorted_dates[idx-1]]
        limit_map[d] = (prev*(1-limit_pct), prev*(1+limit_pct))
    return limit_map


# ============================================================
# 多批次交易模拟
# ============================================================

def _simulate_multi_lot(open_, high, low, close, atr, ts, unique_dates,
                        buy_signal, trade_start,
                        stop_atr, profit_pct, max_hold,
                        buy_ratio, sell_ratio, cooling, capital,
                        limit_map, commission, slippage) -> np.ndarray:
    n = len(close)
    cash = capital
    lots: list[Lot] = []
    last_buy_bar = -999
    curve_len = n - trade_start
    eq_curve = np.zeros(curve_len)

    # 20分钟均量 (用于无量假突破过滤)
    vol_ma20 = pd.Series(volume).rolling(20, min_periods=1).mean().values

    for i in range(n):
        cur_date = ts.iloc[i].date()
        cur_date_idx = unique_dates.index(cur_date)
        lim_down, lim_up = limit_map.get(cur_date, (0, 999999))

        is_limit_up   = close[i] >= lim_up * 0.999
        is_limit_down = close[i] <= lim_down * 1.001
        is_one_word_up = (is_limit_up and
                          abs(open_[i]-close[i])<0.01 and abs(high[i]-close[i])<0.01 and
                          abs(low[i]-close[i])<0.01)

        if i < trade_start:
            continue

        # ── 优先级1: 风控 ──
        new_lots = []
        for lot in lots:
            lot.highest_close = max(lot.highest_close, close[i])
            stop_level   = lot.highest_close - stop_atr * atr[i]
            profit_level = lot.entry_price * (1 + profit_pct)
            bars_held = i - lot.entry_bar
            can_sell = cur_date_idx > lot.entry_date_idx
            force_close = (i == n - 1)

            if ((close[i] < stop_level and can_sell) or
                (close[i] > profit_level and can_sell) or
                (bars_held >= max_hold and can_sell) or
                force_close):
                if is_limit_down and not force_close:
                    new_lots.append(lot); continue
                to_sell = int(lot.shares * sell_ratio)
                if force_close: to_sell = lot.shares
                if to_sell <= 0: to_sell = lot.shares
                exit_price = close[i] * (1 - slippage)
                cash += to_sell * exit_price * (1 - commission - STAMP_TAX)
                lot.shares -= to_sell
                if lot.shares > 0: new_lots.append(lot)
            else:
                new_lots.append(lot)
        lots = new_lots

        # ── 优先级3: 买入 (带硬性物理过滤) ──
        # 硬性过滤 1: 最后一根K线不开新仓
        if i >= n - 2: pass
        # 硬性过滤 2: 极度缩量 — ratio < 0.3, 信号大概率是噪音
        elif volume[i] < vol_ma20[i] * 0.3 and i >= 20: pass
        # 硬性过滤 3: 涨停买不到 / 一字板
        elif is_limit_up and (is_one_word_up or close[i] >= lim_up * 0.995): pass
        elif buy_signal[i] and len(lots) < MAX_LOTS:
            if i - last_buy_bar >= cooling and cash > 0:
                buy_amount = cash * buy_ratio
                buy_price = open_[min(i+1, n-1)] * (1 + slippage)
                shares = int(buy_amount / (buy_price * 100)) * 100
                if shares > 0:
                    cost = shares * buy_price * (1 + commission)
                    if cost <= cash:
                        lots.append(Lot(i, cur_date_idx, shares, buy_price, close[i]))
                        cash -= cost
                        last_buy_bar = i

        holdings_value = sum(l.shares * close[i] for l in lots)
        eq_curve[i - trade_start] = cash + holdings_value

    return eq_curve


# ============================================================
# 净值曲线 → 指标
# ============================================================

def _calc_metrics_from_curve(eq_curve, capital):
    n = len(eq_curve)
    if n < 2: return _empty()
    total_return = (eq_curve[-1] - capital) / capital
    if total_return <= -0.99: return _empty()
    returns = (eq_curve[1:] - eq_curve[:-1]) / eq_curve[:-1]
    std_ret = np.std(returns, ddof=1)
    sharpe = np.mean(returns)/(std_ret+1e-9)*np.sqrt(252*240) if std_ret>1e-9 else 0.0
    peak = np.maximum.accumulate(eq_curve)
    max_dd = float(np.min(eq_curve/peak-1)) if len(eq_curve)>0 else 0.0
    max_dd = max_dd if max_dd<-0.001 else -0.001
    annual_return = (1+total_return)**(252/5)-1
    annual_return = max(annual_return, -0.99)
    calmar = annual_return/abs(max_dd) if max_dd<-0.0001 else annual_return*10
    return {'calmar': float(np.clip(calmar,-100,1000)),
            'sharpe': float(np.clip(sharpe,-50,50)),
            'max_dd': max_dd, 'total_return': total_return,
            'win_rate': 0.0, 'num_trades': 0}


def _empty():
    return {'calmar':0.0,'sharpe':0.0,'max_dd':0.0,'total_return':0.0,'win_rate':0.0,'num_trades':0}


if __name__ == '__main__':
    np.random.seed(42)
    n = BACKTEST_WARMUP + BACKTEST_WINDOW
    idx = pd.date_range('2025-06-09 09:30', periods=n, freq='1min')
    close = 100 + np.cumsum(np.random.randn(n)*0.3); close = np.maximum(close, 10)
    high = close + np.random.rand(n)*1.5; low = close - np.random.rand(n)*1.5
    open_ = close - np.random.randn(n)*0.2
    volume = np.random.randint(10000, 100000, n).astype(float)
    df = pd.DataFrame({'ts':idx,'open':open_,'high':high,'low':low,'close':close,'volume':volume})
    for sid in range(9):
        info = STRATEGY_PARAMS[sid]
        p1=(info['p1_range'][0]+info['p1_range'][1])/2
        p2=(info['p2_range'][0]+info['p2_range'][1])/2
        r=run_backtest(df,sid,p1,p2,stock_code='sh600036')
        print(f"策略{sid} {info['name']}: calmar={r['calmar']:.2f}")
