#!/usr/bin/env python3
"""
多策略回测引擎 v2
==================

这是整个样本生成系统的核心。它接受一段 K 线历史数据 + 策略参数，
模拟交易并返回绩效指标。样本生成器用它来判断"这组参数好不好"，从而
给模型提供训练标签。

关键设计决策
--------------
1. T+1 约束：A 股当天买入的股票最早下个交易日才能卖出。
   实现方式是按交易日日期分组，卖出时校验 cur_date_idx > entry_date_idx。
   **注意**：此约束导致 2 天窗口只能做 1 笔交易，是 Calmar 过低的主因。

2. ATR 追踪止损：止损线 = 持仓期间最高价 - stop_atr * ATR(14)。
   用最高价而非入场价作为基准，跟随上涨自动抬高止损线。

3. 三重风控：止损(stop_atr) + 止盈(profit_pct) + 时间止损(max_hold)，
   任一触发立即平仓。

4. 回测摩擦：千 1.5 双向佣金 + 0.1% 双向滑点 + 涨跌停检测。

数据要求
----------
df 必须包含: ts, open, high, low, close, volume (Pandas DataFrame)

策略矩阵
----------
0: MA 突破 — 快线从下方上穿慢线，适合单边趋势
1: 布林回归 — 价格触碰下轨后反弹，适合震荡市
2: 放量突破 — 成交量放大同时突破近期高点，适合行情启动
3: ATR 通道 — 价格突破均线+ATR通道上沿，适合高波动
4: 动量突破 — 价格动量超过阈值且放量，适合强势行情
"""
import numpy as np
import pandas as pd

# ============================================================
# 回测摩擦成本常量
# ============================================================
COMMISSION = 0.0015   # 千分之 1.5 (买入+卖出各收取一次)
SLIPPAGE  = 0.001     # 0.1% 滑点 (买入时 entry_price 上调, 卖出时 exit_price 下调)

# ============================================================
# 5 策略模板的参数定义
# 每个策略固定 5 个参数:
#   p1, p2  = 策略专属入场信号参数 (如 MA 的快线/慢线周期)
#   p3      = stop_atr, 即 ATR 止损倍数 (1.0~4.0)
#   p4      = profit_pct, 止盈百分比 (0.01~0.10, 即 1%~10%)
#   p5      = max_hold, 最大持仓分钟数 (60~480)
#     ** 持仓超时自动平仓，防止被套后无限持有消耗时间窗口
#     ** 在 2 天回测窗口里 p5=480 基本等于不触发，但 5-10 天窗口里会触发
# ============================================================
STRATEGY_PARAMS = {
    0: {"p1_name": "fast_ma",    "p1_range": (2, 20),      # 快线周期 (2~20分钟)
         "p2_name": "slow_ma",    "p2_range": (20, 120),    # 慢线周期 (20~120分钟)
         "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),   # ATR止损倍数
         "p4_name": "profit_pct", "p4_range": (0.01, 0.10), # 止盈百分比 (1%~10%)
         "p5_name": "max_hold",   "p5_range": (60, 480)},   # 最大持仓分钟数
    1: {"p1_name": "bb_period",  "p1_range": (10, 50),     # 布林带周期
         "p2_name": "bb_std",     "p2_range": (1.5, 3.0),   # 布林带标准差倍数
         "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
         "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
         "p5_name": "max_hold",   "p5_range": (60, 480)},
    2: {"p1_name": "vol_ratio",  "p1_range": (1.5, 5.0),   # 量比阈值 (成交量/均量)
         "p2_name": "lookback",   "p2_range": (5, 30),      # 回看周期 (计算前高和前均量)
         "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
         "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
         "p5_name": "max_hold",   "p5_range": (60, 480)},
    3: {"p1_name": "atr_period", "p1_range": (5, 30),      # ATR 计算周期
         "p2_name": "atr_mult",   "p2_range": (1.5, 4.0),   # ATR 通道倍数
         "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
         "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
         "p5_name": "max_hold",   "p5_range": (60, 480)},
    4: {"p1_name": "mom_period", "p1_range": (5, 30),      # 动量计算周期
         "p2_name": "mom_thresh", "p2_range": (0.02, 0.10), # 动量阈值 (2%~10%)
         "p3_name": "stop_atr",   "p3_range": (1.0, 4.0),
         "p4_name": "profit_pct", "p4_range": (0.01, 0.10),
         "p5_name": "max_hold",   "p5_range": (60, 480)},
}

STRATEGY_NAMES = {0: "MA突破", 1: "布林回归", 2: "放量突破", 3: "ATR通道", 4: "动量突破"}


# ============================================================
# 停牌/缺口检测
# ============================================================

def has_gap(df: pd.DataFrame, max_gap_hours: int = 72) -> bool:
    """
    检测 K 线序列中是否有超过 max_gap_hours 小时的时间断层。

    用途: 排除停牌日或数据缺失日对应的回测窗口。
    A 股周末休市 ~66 小时 (周五 15:00 → 周一 9:30)，午休 1.5 小时。
    默认阈值 72 小时能区分"正常周末"和"停牌缺口"。

    返回 True 表示有缺口，该窗口应被跳过。
    """
    if 'ts' not in df.columns or len(df) < 2:
        return False
    gaps = df['ts'].diff().dropna()
    # 计算相邻两根 K 线之间的时间差（小时）
    return (gaps.dt.total_seconds() / 3600 > max_gap_hours).any()


# ============================================================
# 主回测入口
# ============================================================

def run_backtest(df: pd.DataFrame, strategy_id: int,
                 p1: float, p2: float, p3: float,
                 p4: float = 0.05, p5: float = 240,
                 commission: float = COMMISSION, slippage: float = SLIPPAGE):
    """
    在 K 线窗口上执行完整回测。

    参数
    ----
    df        : 原始 K 线 DataFrame，必须包含 ts, open, high, low, close, volume
    strategy_id: 策略 ID (0-4)
    p1-p5     : 策略参数，含义见 STRATEGY_PARAMS
    commission: 佣金费率
    slippage  : 滑点费率

    返回
    ----
    dict: {calmar, sharpe, max_dd, total_return, win_rate, num_trades}
    如果数据不足(<60条)或无交易，返回全零字典。

    流程
    ----
    1. 提取 numpy 数组 (性能优化)
    2. 计算涨跌停线 (基于窗口首根 K 线的前收盘价)
    3. 计算 ATR(14) 用于止损
    4. 根据策略生成买入信号布尔数组
    5. 按交易日分组实现 T+1 约束
    6. 逐根 K 线模拟交易 (单持仓)
    7. 根据交易记录计算绩效指标
    """
    n = len(df)
    if n < 60:   # 数据太少，无法形成有效交易
        return _empty()

    # ── 1. 提取 numpy 数组 (避免 pandas 索引开销，向量化计算快) ──
    ts = pd.to_datetime(df['ts'])
    close = df['close'].values.astype(float)
    high  = df['high'].values.astype(float)
    low   = df['low'].values.astype(float)
    open_ = df['open'].values.astype(float)
    volume = df['volume'].values.astype(float)

    # ── 2. 涨跌停线 ──
    # 以窗口首根 K 线的前收盘价 (取首根 close 近似) 为基准计算 ±10%
    # 涨停价买不到 (涨停板封死)，跌停价卖不掉 (跌停板封死)
    pre_close = close[0]
    limit_up   = pre_close * 1.10
    limit_down = pre_close * 0.90

    # ── 3. ATR(14) 用于止损 ──
    # ATR 衡量波动率，止损线 = 持仓期间最高价 - stop_atr * ATR
    atr = _calc_atr(high, low, close, 14)

    # ── 4. 生成买入信号布尔数组 ──
    sid = int(strategy_id)
    buy = _gen_buy_signal(sid, close, high, low, volume, p1, p2)

    # ── 5. 按交易日分组 ──
    # 把 ts 转成日期，用于实现 T+1 (买入日和卖出日必须不同)
    dates = ts.dt.date
    unique_dates = sorted(dates.unique())

    # ── 6. 模拟交易 ──
    trades = _simulate(
        open_, high, low, close, atr,
        ts, unique_dates,
        buy, p3, p4, int(p5),
        limit_up, limit_down, commission, slippage
    )

    # ── 7. 计算绩效 ──
    return _calc_metrics(trades)


# ============================================================
# 买入信号生成 (纯向量化)
# ============================================================

def _gen_buy_signal(sid, close, high, low, volume, p1, p2):
    """
    根据策略 ID 和参数，生成一个布尔数组表示每根 K 线是否有买入信号。

    这是整个回测引擎的"策略逻辑层"。每个策略的买入信号:
    - 策略 0 (MA 突破)   : 快线从下方上穿慢线
    - 策略 1 (布林回归)   : 价格 ≤ 布林下轨且本根 K 线收涨
    - 策略 2 (放量突破)   : 量比 > p1 且价格突破近 p2 根 K 线的最高价
    - 策略 3 (ATR 通道)   : 价格 > MA(p1) + p2 * ATR(p1)
    - 策略 4 (动量突破)   : 价格动量 > p2 且成交量放大

    返回: 长度为 n 的布尔 numpy 数组
    """
    n = len(close)

    if sid == 0:   # MA 突破 (双均线金叉)
        f, s = int(p1), int(p2)
        if f >= s or s >= n:
            return np.zeros(n, dtype=bool)

        # 计算快慢均线
        mf = pd.Series(close).rolling(f, min_periods=1).mean().values
        ms = pd.Series(close).rolling(s, min_periods=1).mean().values

        # ★ 金叉条件: 当前快线 > 慢线，且上一根快线 ≤ 慢线 (上穿)
        # ⚠  BUG: np.roll 是循环移位, 第0个元素会和最后一个比较
        #    修复见 optimization_plan.md 2.5 节
        return (mf > ms) & (np.roll(mf, 1) <= np.roll(ms, 1))

    elif sid == 1: # 布林回归 (下轨反弹)
        p = int(p1)
        if p >= n:
            return np.zeros(n, dtype=bool)

        # 布林下轨 = MA(p) - p2 * std(p)
        s = pd.Series(close)
        mid = s.rolling(p, min_periods=1).mean().values
        std = s.rolling(p, min_periods=1).std().values
        lower = mid - p2 * std

        # 信号: 触及下轨 且 本根收涨 (确认反弹而非继续下跌)
        return (close <= lower) & (close > np.roll(close, 1))

    elif sid == 2: # 放量突破 (量价共振)
        lb = int(p2)
        if lb >= n:
            return np.zeros(n, dtype=bool)

        # 近 lb 根 K 线的均量和最高价
        vma = pd.Series(volume).rolling(lb, min_periods=1).mean().values
        hh  = pd.Series(high).rolling(lb, min_periods=1).max().values

        # 信号: 当前量 > 均量 × p1 且 当前价突破近 lb 根的最高价
        return (volume > vma * p1) & (close > np.roll(hh, 1))

    elif sid == 3: # ATR 通道突破
        ap = int(p1)
        if ap >= n:
            return np.zeros(n, dtype=bool)

        # 通道上沿 = MA(ap) + p2 * ATR(ap)
        ma = pd.Series(close).rolling(ap, min_periods=1).mean().values
        atr_v = _calc_atr(high, low, close, ap)
        return close > (ma + p2 * atr_v)

    elif sid == 4: # 动量突破
        mp = int(p1)
        if mp >= n:
            return np.zeros(n, dtype=bool)

        # 价格动量: (当前价 - mp 根前) / mp 根前
        mom = (close - np.roll(close, mp)) / (np.roll(close, mp) + 1e-9)

        # 量能确认: 当前成交量 > mp 根前的均量
        vma = pd.Series(volume).rolling(mp, min_periods=1).mean().values
        return (mom > p2) & (volume > np.roll(vma, 1))

    return np.zeros(n, dtype=bool)


# ============================================================
# ATR (Average True Range) 计算
# ============================================================

def _calc_atr(high, low, close, period):
    """
    使用 EMA 方式计算 ATR。

    True Range = max(H-L, |H-prev_close|, |L-prev_close|)
    ATR = EMA(TR, alpha = 1/period)

    第 0 根 K 线没有前收盘价，TR = H - L。
    """
    pc = np.roll(close, 1)   # ⚠ 同样有 np.roll 边界问题，但第0根单独处理
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    tr[0] = high[0] - low[0]  # 第0根特殊处理
    return pd.Series(tr).ewm(alpha=1/period, min_periods=period).mean().values


# ============================================================
# 交易模拟 (核心逻辑)
# ============================================================

def _simulate(open_, high, low, close, atr,
              ts, unique_dates,
              buy, stop_atr, profit_pct, max_hold,
              limit_up, limit_down, commission, slippage):
    """
    逐根 K 线模拟单持仓交易。

    状态变量
    ----------
    in_position     : 是否持仓 (当前只支持单持仓)
    entry_price     : 入场价 (以下一根 open 成交，+滑点)
    entry_bar       : 入场 K 线索引
    highest_close   : 持仓期间最高收盘价 (用于追踪止损)
    entry_date_idx  : 入场日在 unique_dates 中的索引 (用于 T+1 校验)

    卖出触发条件 (优先级从高到低)
    --------------------------------
    1. 止盈:  当前价 > entry_price * (1 + profit_pct)  (需满足 T+1)
    2. 止损:  当前价 < highest_close - stop_atr * ATR   (需满足 T+1，追踪式)
    3. 超时:  持仓分钟数 > max_hold                     (需满足 T+1)
    4. 强制:  到达窗口末尾 (不受 T+1 限制)

    关键: T+1 约束通过 can_sell 实现
    - cur_date_idx > entry_date_idx 才允许卖出
    - 如果入场日是唯一或最后交易日，窗口末强制平仓不受 T+1 限制
    """
    trades = []
    in_position = False
    entry_price = entry_bar = highest_close = 0.0

    for i in range(len(close)):
        # ── 当前日期信息 ──
        cur_date = ts.iloc[i].date()
        cur_date_idx = unique_dates.index(cur_date)

        # ── 涨跌停检测 ──
        # 涨停价买不到 (涨停封板)，跌停价不卖 (卖不掉) —— 但止损在跌停日触发时跳过
        is_limit_up   = close[i] >= limit_up * 0.999
        is_limit_down = close[i] <= limit_down * 1.001

        if not in_position:
            # ====== 空仓状态: 检查买入信号 ======
            if buy[i] and not is_limit_up:
                # 以下一根 K 线的开盘价成交 (+滑点)，模拟真实下单延迟
                entry_idx = min(i + 1, len(close) - 1)
                in_position = True
                entry_price = open_[entry_idx] * (1 + slippage)
                entry_bar = i
                highest_close = close[i]
                entry_date_idx = cur_date_idx
        else:
            # ====== 持仓状态: 检查卖出条件 ======
            # 追踪止损: 止损线随最高价上移，不会下移
            highest_close = max(highest_close, close[i])

            # 止损价 = 持仓期间最高价 - stop_atr × 当前 ATR
            stop_level = highest_close - stop_atr * atr[i]

            # 止盈价 = 入场价 × (1 + 止盈百分比)
            profit_level = entry_price * (1 + profit_pct)

            # 已持仓 K 线数量
            bars_held = i - entry_bar

            # T+1 核心逻辑: 只有次日及之后才能卖出
            # 如果只有一个交易日 (cur_date_idx 恒等于 entry_date_idx)，只有
            # 窗口末尾强制平仓 (trigger_end) 能触发卖出
            can_sell = (cur_date_idx > entry_date_idx)

            # 四种卖出触发条件 (任一满足 + T+1 约束 或 窗口末尾)
            trigger_stop   = (close[i] < stop_level) and can_sell
            trigger_profit = (close[i] > profit_level) and can_sell
            trigger_time   = (bars_held >= max_hold) and can_sell
            trigger_end    = (i == len(close) - 1)  # 窗口末尾强平，不受 T+1 限制

            should_exit = trigger_stop or trigger_profit or trigger_time or trigger_end

            if should_exit:
                exit_price = close[i] * (1 - slippage)   # 卖出价 - 滑点

                # 净收益 = (卖出价 - 买入价) / 买入价 - 双向佣金
                # 佣金买入时已计在 entry_price 中，所以只减一次 commission
                ret = (exit_price - entry_price) / entry_price - commission * 2

                exit_reason = ('stop' if trigger_stop else
                               'profit' if trigger_profit else
                               'time' if trigger_time else
                               'end' if trigger_end else 'signal')

                trades.append({
                    'entry_bar': entry_bar,
                    'exit_bar': i,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'return': ret,
                    'exit_reason': exit_reason,
                })
                in_position = False

    return trades


# ============================================================
# 绩效指标计算
# ============================================================

def _calc_metrics(trades):
    """
    从交易记录计算绩效指标。

    Calmar Ratio（卡玛比率）
    -------------------------
    = 年化收益 / 最大回撤绝对值
    衡量"每承受 1 单位回撤能获得多少收益"。
    我们的 Optuna 优化目标就是最大化 Calmar。

    计算流程:
    1. 将所有交易的收益率连乘得到累计收益曲线
    2. 寻找累计收益的峰值和峰后的最大回撤
    3. max_dd = min(累计/峰值 - 1)，即从峰值往下的最大跌幅
    4. 年化收益 = (1 + 总收益)^(252 / 交易次数) - 1

    Sharpe Ratio（夏普比率）
    -------------------------
    = 平均收益率 / 收益率标准差 × sqrt(252)
    衡量单位风险下的超额回报。

    注意: 至少需要 2 笔交易才能计算标准差，否则返回全零。
    """
    if not trades or len(trades) < 2:
        return _empty()

    returns = np.array([t['return'] for t in trades])
    total_return = np.prod(1 + returns) - 1   # 复利总收益
    win_rate = np.mean(returns > 0)             # 胜率: 盈利交易占比
    n = len(returns)

    # ── Sharpe Ratio ──
    std_ret = np.std(returns, ddof=1)   # 样本标准差
    sharpe = np.mean(returns) / (std_ret + 1e-9) * np.sqrt(252) if std_ret > 1e-9 else 0.0

    # ── Max Drawdown (最大回撤) ──
    cumulative = np.cumprod(1 + returns)             # 累计收益曲线
    peak = np.maximum.accumulate(cumulative)          # 历史最高净值 (每个时点)
    max_dd = float(np.min(cumulative / peak - 1)) if len(cumulative) > 0 else 0.0
    max_dd = max_dd if max_dd < -0.001 else -0.001    # 确保有非零回撤

    # ── Calmar Ratio ──
    # 年化收益 = 复利总收益 按交易笔数折算成年度
    annual_return = (1 + total_return) ** (252 / max(n, 2)) - 1
    annual_return = max(annual_return, -0.99)   # 年化亏损不超过 -99%
    calmar = annual_return / abs(max_dd) if max_dd < -0.0001 else annual_return * 10

    # Clip 防止极端值
    calmar = float(np.clip(calmar, -100, 1000))
    sharpe = float(np.clip(sharpe, -50, 50))

    return {
        'calmar': calmar, 'sharpe': sharpe, 'max_dd': max_dd,
        'total_return': total_return, 'win_rate': win_rate, 'num_trades': n,
    }


def _empty():
    """
    返回空结果 (无交易时的默认值)。
    所有指标为 0，Calmar=0 在 Optuna 中表示无效 trial。
    """
    return {'calmar': 0.0, 'sharpe': 0.0, 'max_dd': 0.0,
            'total_return': 0.0, 'win_rate': 0.0, 'num_trades': 0}


# ============================================================
# 自测入口
# ============================================================

if __name__ == '__main__':
    # 生成 480 根随机模拟 K 线，快速验证 5 个策略的回测逻辑
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
        # 取参数范围的中间值作为默认测试参数
        p1 = (info['p1_range'][0] + info['p1_range'][1]) / 2
        p2 = (info['p2_range'][0] + info['p2_range'][1]) / 2
        p3 = (info['p3_range'][0] + info['p3_range'][1]) / 2
        r = run_backtest(df, sid, p1, p2, p3, p4=0.05, p5=240)
        print(f"策略{sid} {STRATEGY_NAMES[sid]}: trades={r['num_trades']}, "
              f"calmar={r['calmar']:.2f}, sharpe={r['sharpe']:.2f}, "
              f"dd={r['max_dd']:.3f}, ret={r['total_return']:.3f}")
