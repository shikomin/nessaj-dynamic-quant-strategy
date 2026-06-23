#!/usr/bin/env python3
"""
多策略回测引擎 v2
5 策略模板 + T+1(按日期分组) + ATR止损 + 止盈 + 时间止损 + 滑点 + 涨跌停检测 + 缺口检测
纯向量化计算，单个回测窗口 < 10ms
"""
import numpy as np
import pandas as pd

COMMISSION = 0.0015   # 千1.5
SLIPPAGE  = 0.001     # 0.1% 滑点

# ── 策略参数定义 (每策略5参数) ──
STRATEGY_PARAMS = {
    0: {"p1_name": "fast_ma",    "p1_range": (2, 20),
        "p2_name": "slow_ma",    "p2_range": (20, 120),
        "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
        "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
        "p5_name": "max_hold",   "p5_range": (60, 480)},
    1: {"p1_name": "bb_period",  "p1_range": (10, 50),
        "p2_name": "bb_std",     "p2_range": (1.5, 3.0),
        "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
        "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
        "p5_name": "max_hold",   "p5_range": (60, 480)},
    2: {"p1_name": "vol_ratio",  "p1_range": (1.5, 5.0),
        "p2_name": "lookback",   "p2_range": (5, 30),
        "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
        "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
        "p5_name": "max_hold",   "p5_range": (60, 480)},
    3: {"p1_name": "atr_period", "p1_range": (5, 30),
        "p2_name": "atr_mult",   "p2_range": (1.5, 4.0),
        "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
        "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
        "p5_name": "max_hold",   "p5_range": (60, 480)},
    4: {"p1_name": "mom_period", "p1_range": (5, 30),
        "p2_name": "mom_thresh", "p2_range": (0.02, 0.10),
        "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
        "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
        "p5_name": "max_hold",   "p5_range": (60, 480)},
}

STRATEGY_NAMES = {0: "MA突破", 1: "布林回归", 2: "放量突破", 3: "ATR通道", 4: "动量突破"}


def has_gap(df: pd.DataFrame, max_gap_hours: int = 72) -> bool:
    """检测是否有超过 72 小时的缺口（多日停牌），忽略周末隔夜（~66h）和午休"""
    if 'ts' not in df.columns or len(df) < 2:
        return False
    gaps = df['ts'].diff().dropna()
    return (gaps.dt.total_seconds() / 3600 > max_gap_hours).any()


def run_backtest(df: pd.DataFrame, strategy_id: int,
                 p1: float, p2: float, p3: float,
                 p4: float = 0.05, p5: float = 240,
                 commission: float = COMMISSION, slippage: float = SLIPPAGE):
    """
    在 K 线窗口上回测。

    df: 必需 ts, open, high, low, close, volume
    p1-p5: 策略参数
    p4: 止盈百分比 (0.01~0.10)
    p5: 最大持仓分钟数 (60~480)

    返回: {calmar, sharpe, max_dd, total_return, win_rate, num_trades}
    """
    n = len(df)
    if n < 60:
        return _empty()

    # 提取数组
    ts = pd.to_datetime(df['ts'])
    close = df['close'].values.astype(float)
    high  = df['high'].values.astype(float)
    low   = df['low'].values.astype(float)
    open_ = df['open'].values.astype(float)
    volume = df['volume'].values.astype(float)

    # ── 涨跌停线 (粗略: 按前收盘 ±10%) ──
    pre_close = close[0]
    limit_up   = pre_close * 1.10
    limit_down = pre_close * 0.90

    # ── ATR ──
    atr = _calc_atr(high, low, close, 14)

    # ── 买入信号 ──
    sid = int(strategy_id)
    buy = _gen_buy_signal(sid, close, high, low, volume, p1, p2)

    # ── 按交易日分组 (T+1) ──
    dates = ts.dt.date
    unique_dates = sorted(dates.unique())

    # ── 模拟交易 ──
    trades = _simulate(
        open_, high, low, close, atr,
        ts, unique_dates,
        buy, p3, p4, int(p5),
        limit_up, limit_down, commission, slippage
    )

    return _calc_metrics(trades)


# ── 买入信号 ──

def _gen_buy_signal(sid, close, high, low, volume, p1, p2):
    n = len(close)
    if sid == 0:   # MA 突破
        f, s = int(p1), int(p2)
        if f >= s or s >= n: return np.zeros(n, dtype=bool)
        mf = pd.Series(close).rolling(f, min_periods=1).mean().values
        ms = pd.Series(close).rolling(s, min_periods=1).mean().values
        return (mf > ms) & (np.roll(mf, 1) <= np.roll(ms, 1))
    elif sid == 1: # 布林回归
        p = int(p1)
        if p >= n: return np.zeros(n, dtype=bool)
        s = pd.Series(close)
        mid = s.rolling(p, min_periods=1).mean().values
        std = s.rolling(p, min_periods=1).std().values
        lower = mid - p2 * std
        return (close <= lower) & (close > np.roll(close, 1))
    elif sid == 2: # 放量突破
        lb = int(p2)
        if lb >= n: return np.zeros(n, dtype=bool)
        vma = pd.Series(volume).rolling(lb, min_periods=1).mean().values
        hh  = pd.Series(high).rolling(lb, min_periods=1).max().values
        return (volume > vma * p1) & (close > np.roll(hh, 1))
    elif sid == 3: # ATR 通道
        ap = int(p1)
        if ap >= n: return np.zeros(n, dtype=bool)
        ma = pd.Series(close).rolling(ap, min_periods=1).mean().values
        atr_v = _calc_atr(high, low, close, ap)
        return close > (ma + p2 * atr_v)
    elif sid == 4: # 动量突破
        mp = int(p1)
        if mp >= n: return np.zeros(n, dtype=bool)
        mom = (close - np.roll(close, mp)) / (np.roll(close, mp) + 1e-9)
        vma = pd.Series(volume).rolling(mp, min_periods=1).mean().values
        return (mom > p2) & (volume > np.roll(vma, 1))
    return np.zeros(n, dtype=bool)


# ── ATR ──

def _calc_atr(high, low, close, period):
    pc = np.roll(close, 1)
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    tr[0] = high[0] - low[0]
    return pd.Series(tr).ewm(alpha=1/period, min_periods=period).mean().values


# ── 交易模拟 ──

def _simulate(open_, high, low, close, atr,
              ts, unique_dates,
              buy, stop_atr, profit_pct, max_hold,
              limit_up, limit_down, commission, slippage):
    trades = []
    in_position = False
    entry_price = entry_bar = highest_close = 0.0

    for i in range(len(close)):
        cur_date = ts.iloc[i].date()
        cur_date_idx = unique_dates.index(cur_date)

        # ── 涨跌停检查 ──
        is_limit_up   = close[i] >= limit_up * 0.999
        is_limit_down = close[i] <= limit_down * 1.001

        if not in_position:
            # 买入信号
            if buy[i] and not is_limit_up:
                # T+1 出口: 买入后只能在下一交易日平仓
                # 当前就标记持仓，卖出逻辑里校验日期
                entry_idx = min(i + 1, len(close) - 1)
                in_position = True
                entry_price = open_[entry_idx] * (1 + slippage)  # 滑点
                entry_bar = i
                highest_close = close[i]
                entry_date_idx = cur_date_idx
        else:
            # ── 持仓中 ──
            highest_close = max(highest_close, close[i])
            stop_level = highest_close - stop_atr * atr[i]
            profit_level = entry_price * (1 + profit_pct)
            bars_held = i - entry_bar

            # 卖出条件 (任一触发 + T+1约束)
            can_sell = (cur_date_idx > entry_date_idx)  # T+1: 次日才能卖
            trigger_stop   = (close[i] < stop_level) and can_sell
            trigger_profit = (close[i] > profit_level) and can_sell
            trigger_time   = (bars_held >= max_hold) and can_sell
            trigger_end    = (i == len(close) - 1)  # 末尾强制平仓不受 T+1 限制

            should_exit = trigger_stop or trigger_profit or trigger_time or trigger_end

            if should_exit:
                exit_price = close[i] * (1 - slippage)
                ret = (exit_price - entry_price) / entry_price - commission * 2
                trades.append({
                    'entry_bar': entry_bar, 'exit_bar': i,
                    'entry_price': entry_price, 'exit_price': exit_price,
                    'return': ret,
                    'exit_reason': 'stop' if trigger_stop else
                                   'profit' if trigger_profit else
                                   'time' if trigger_time else
                                   'end' if trigger_end else 'signal',
                })
                in_position = False

    return trades


# ── 指标计算 ──

def _calc_metrics(trades):
    if not trades or len(trades) < 2:
        return _empty()

    returns = np.array([t['return'] for t in trades])
    total_return = np.prod(1 + returns) - 1
    win_rate = np.mean(returns > 0)
    n = len(returns)

    std_ret = np.std(returns, ddof=1)
    sharpe = np.mean(returns) / (std_ret + 1e-9) * np.sqrt(252) if std_ret > 1e-9 else 0.0

    cumulative = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cumulative)
    max_dd = float(np.min(cumulative / peak - 1)) if len(cumulative) > 0 else 0.0
    max_dd = max_dd if max_dd < -0.001 else -0.001

    annual_return = (1 + total_return) ** (252 / max(n, 2)) - 1
    annual_return = max(annual_return, -0.99)
    calmar = annual_return / abs(max_dd) if max_dd < -0.0001 else annual_return * 10

    calmar = float(np.clip(calmar, -100, 1000))
    sharpe = float(np.clip(sharpe, -50, 50))

    return {
        'calmar': calmar, 'sharpe': sharpe, 'max_dd': max_dd,
        'total_return': total_return, 'win_rate': win_rate, 'num_trades': n,
    }


def _empty():
    return {'calmar': 0.0, 'sharpe': 0.0, 'max_dd': 0.0,
            'total_return': 0.0, 'win_rate': 0.0, 'num_trades': 0}


# ── 自测 ──
if __name__ == '__main__':
    np.random.seed(42)
    n = 480
    idx = pd.date_range('2025-06-09 09:30', periods=n, freq='1min')
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    close = np.maximum(close, 10)
    high = close + np.random.rand(n) * 2
    low = close - np.random.rand(n) * 2
    open_ = close - np.random.randn(n) * 0.3
    volume = np.random.randint(10000, 100000, n).astype(float)

    df = pd.DataFrame({'ts': idx, 'open': open_, 'high': high, 'low': low, 'close': close, 'volume': volume})

    for sid in range(5):
        info = STRATEGY_PARAMS[sid]
        p1 = (info['p1_range'][0] + info['p1_range'][1]) / 2
        p2 = (info['p2_range'][0] + info['p2_range'][1]) / 2
        p3 = (info['p3_range'][0] + info['p3_range'][1]) / 2
        r = run_backtest(df, sid, p1, p2, p3, p4=0.05, p5=240)
        print(f"策略{sid} {STRATEGY_NAMES[sid]}: trades={r['num_trades']}, "
              f"calmar={r['calmar']:.2f}, sharpe={r['sharpe']:.2f}, "
              f"dd={r['max_dd']:.3f}, ret={r['total_return']:.3f}")
