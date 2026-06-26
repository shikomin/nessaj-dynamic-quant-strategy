#!/usr/bin/env python3
"""
特征工程模块 v2.3 (32维)
==========================

从原始 1分钟 K 线 + 指数 1分钟K线 + 市场情绪 → 计算 32 维特征。

===================================================================
v2.2 新增: 大盘特征
--------------------
从 index_kline_1m 超级表读取指数数据, 按个股所属板块自动匹配:
  sh60/s68 → sh000001 (上证综指)
  sz00     → sz399001 (深证成指)
  sz30     → sz399006 (创业板指)
  bj       → sh000001 (北交所对上证)

计算 3 维大盘特征:
  index_change_pct   : 指数分钟涨跌幅
  relative_strength  : 个股相对指数的涨跌幅
  index_volume_ratio : 指数量比

===================================================================
29维特征明细
--------------
| 类别       | 特征                           | 维度 |
|------------|--------------------------------|------|
| 价格基础   | open, high, low, close         | 4    |
| 成交量     | volume, volume_ma5, volume_ratio | 3  |
| 趋势       | ma5, ma10, ma20, ma60          | 4    |
| 动量       | rsi_6, rsi_14, macd, macd_signal, macd_hist | 5 |
| 波动       | atr_14, bb_upper, bb_lower, bb_width | 4    |
| 量价       | obv, mfi_14                    | 2    |
| 市场结构   | amplitude_pct, volume_momentum, price_position | 3 |
| 情绪       | sentiment                      | 1    |
| 大盘       | index_change_pct, relative_strength, index_volume_ratio | 3 |
| **总计**   |                                | **29** |

===================================================================
依赖
-----
输入:
  TDengine m_{code}           — 个股原始 1分钟K线
  TDengine i_{index_code}     — 指数 1分钟K线 (v2.2)
  TDengine sent_daily         — 市场情绪日K线

输出:
  TDengine f_{code}           — 29维特征

用法:
  python feature_engineering.py                  # 全量计算
  python feature_engineering.py --stock sh600036  # 单股
"""
import sys
import time
import logging
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# 将项目根目录加入 sys.path, 使 components/ 和 utils/ 模块可被导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.config import load_config, PROJECT_ROOT
from components.logger import setup_logging
from components.td_connector import TdConnector
from utils.proxy_utils import disable_system_proxy
from utils.data_utils import load_stock_list

# ============================================================
# 新版 ALL_COLS_FEATURE (32维, 与 features_1m 超级表列顺序一致)
# ============================================================
ALL_COLS_FEATURE_V2 = [
    # 价格基础 (4)
    'open', 'high', 'low', 'close',
    # 成交量 (3)
    'volume', 'volume_ma5', 'volume_ratio',
    # 趋势 (4)
    'ma5', 'ma10', 'ma20', 'ma60',
    # 动量 (5)
    'rsi_6', 'rsi_14', 'macd', 'macd_signal', 'macd_hist',
    # 波动 (4)
    'atr_14', 'bb_upper', 'bb_lower', 'bb_width',
    # 量价 (2)
    'obv', 'mfi_14',
    # 市场结构 (3)
    'amplitude_pct', 'volume_momentum', 'price_position',
    # 情绪 (1)
    'sentiment',
    # v2.2 大盘特征 (3)
    'index_change_pct', 'relative_strength', 'index_volume_ratio',
    # v2.3 截面排名 (3)
    'rank_ma5', 'rank_rsi_14', 'rank_volume',
]

# ============================================================
# 个股 → 指数映射
# ============================================================

def get_index_code(stock_code: str) -> str:
    """
    根据个股代码返回其对应的指数代码。

    映射规则:
      sh60xxxx / sh688xxx → sh000001 (上证综指)
      sz00xxxx            → sz399001 (深证成指)
      sz30xxxx            → sz399006 (创业板指)
      bjxxxxxx            → sh000001 (北交所暂用上证)
    """
    code = stock_code.lower()
    if code.startswith('sh'):
        return 'sh000001'
    if code.startswith('sz'):
        if code[2:].startswith('30'):
            return 'sz399006'   # 创业板
        return 'sz399001'       # 深主板
    if code.startswith('bj'):
        return 'sh000001'       # 北交所对上证
    return 'sh000001'           # 兜底


# ============================================================
# 特征计算
# ============================================================

def compute_features(
    df_stock: pd.DataFrame,
    df_index: pd.DataFrame,
    sentiment_map: dict,
) -> pd.DataFrame:
    """
    从个股K线 + 指数K线 + 情绪数据 → 计算 29 维特征。

    参数
    ----
    df_stock      : 个股原始K线 (ts, open, high, low, close, volume)
    df_index      : 对应指数原始K线 (ts, open, high, low, close, volume)
    sentiment_map : {YYYYMMDD: 情绪值}

    返回
    ----
    DataFrame: 原始K线列 + 29维特征列, 已 ffill + fillna(0)
    """
    if df_stock.empty:
        return df_stock

    df = df_stock.copy()
    open_  = df['open']
    high   = df['high']
    low    = df['low']
    close  = df['close']
    volume = df['volume'].astype(float)

    # ============================================================
    # 成交量特征 (3维)
    # ============================================================
    df['volume_ma5'] = volume.rolling(5, min_periods=1).mean()
    df['volume_ratio'] = volume / df['volume_ma5'].replace(0, np.nan)

    # ============================================================
    # 趋势特征: 多周期 MA (4维)
    # ============================================================
    for p in [5, 10, 20, 60]:
        df[f'ma{p}'] = close.rolling(p, min_periods=1).mean()

    # ============================================================
    # 动量特征: RSI (2维)
    # ============================================================
    df['rsi_6']  = _rsi(close, 6)
    df['rsi_14'] = _rsi(close, 14)

    # ============================================================
    # 动量特征: MACD (3维)
    # ============================================================
    df['macd'], df['macd_signal'], df['macd_hist'] = _macd(close)

    # ============================================================
    # 波动特征: ATR (1维)
    # ============================================================
    df['atr_14'] = _atr(high, low, close, 14)

    # ============================================================
    # 波动特征: 布林带 (3维)
    # ============================================================
    _, u, l, w = _bollinger(close, 20, 2)
    df['bb_upper'] = u
    df['bb_lower'] = l
    df['bb_width'] = w

    # ============================================================
    # 量价特征: OBV (1维)
    # ============================================================
    df['obv'] = _obv(close, volume)

    # ============================================================
    # 量价特征: MFI (1维)
    # ============================================================
    df['mfi_14'] = _mfi(high, low, close, volume, 14)

    # ============================================================
    # 市场结构特征 (3维)
    # ============================================================
    df['amplitude_pct'] = (high - low) / close.replace(0, np.nan) * 100
    vol_ma20 = volume.rolling(20, min_periods=1).mean()
    df['volume_momentum'] = volume / vol_ma20.replace(0, np.nan)
    h_l_range = (high - low).replace(0, np.nan)
    df['price_position'] = (close - low) / h_l_range

    # ============================================================
    # 情绪特征 (1维)
    # ============================================================
    df['date_key'] = df['ts'].dt.strftime('%Y%m%d')
    df['sentiment'] = df['date_key'].map(sentiment_map).fillna(0)
    df = df.drop(columns=['date_key'])

    # ============================================================
    # v2.2 新增: 大盘特征 (3维)
    # ============================================================
    # 对齐时间戳: 以个股 ts 为基准, merge_asof 取最近的指数行
    df = _merge_index_features(df, df_index)

    # ============================================================
    # NaN 处理
    # ============================================================
    fill_cols = [c for c in ALL_COLS_FEATURE_V2
                 if c not in ('ts', 'open', 'high', 'low', 'close', 'volume')
                 and c in df.columns]
    df[fill_cols] = df[fill_cols].ffill().fillna(0)

    # 只保留需要的列
    keep_cols = ['ts'] + ALL_COLS_FEATURE_V2
    return df[[c for c in keep_cols if c in df.columns]]


def _merge_index_features(df_stock: pd.DataFrame, df_index: pd.DataFrame) -> pd.DataFrame:
    """
    将指数特征合并到个股 DataFrame。

    步骤:
    1. 计算指数分钟涨跌幅 (index_change_pct)
    2. 计算指数分钟量比 (index_volume_ratio)
    3. merge_asof 按时间对齐
    4. 计算 relative_strength = 个股涨跌幅 - 指数涨跌幅

    处理逻辑:
    - 如果 df_index 为空 (指数数据缺失), 3 维大盘特征全部填 0
    - merge_asof 使用 backward 方向, 即取 ≤ 当前时间的最新指数行
    """
    if df_index.empty:
        df_stock['index_change_pct'] = 0.0
        df_stock['relative_strength'] = 0.0
        df_stock['index_volume_ratio'] = 0.0
        return df_stock

    # ── 计算指数涨跌幅 ──
    idx_close = df_index['close'].astype(float)
    df_index['_idx_pct'] = (idx_close - idx_close.shift(1)) / idx_close.shift(1).replace(0, np.nan) * 100
    df_index['_idx_pct'] = df_index['_idx_pct'].fillna(0)

    # ── 计算指数量比 ──
    idx_vol = df_index['volume'].astype(float)
    idx_vol_ma5 = idx_vol.rolling(5, min_periods=1).mean()
    df_index['_idx_vol_ratio'] = idx_vol / idx_vol_ma5.replace(0, np.nan)
    df_index['_idx_vol_ratio'] = df_index['_idx_vol_ratio'].fillna(1)

    # ── 时间对齐: 以个股 ts 为准, 取最近的指数行 ──
    df_stock['_ts_temp'] = pd.to_datetime(df_stock['ts'])
    df_index['_ts_temp'] = pd.to_datetime(df_index['ts'])

    df_merged = pd.merge_asof(
        df_stock.sort_values('_ts_temp'),
        df_index[['_ts_temp', '_idx_pct', '_idx_vol_ratio']].sort_values('_ts_temp'),
        on='_ts_temp',
        direction='backward',    # 取 ≤ 当前时间的最近指数行
        tolerance=pd.Timedelta(seconds=30),  # 30s 容差
    )

    # ── 计算相对强弱 ──
    stock_pct = (df_merged['close'] - df_merged['close'].shift(1)) / \
                 df_merged['close'].shift(1).replace(0, np.nan) * 100
    stock_pct = stock_pct.fillna(0)

    df_merged['index_change_pct'] = df_merged['_idx_pct'].fillna(0)
    df_merged['relative_strength'] = stock_pct - df_merged['index_change_pct']
    df_merged['index_volume_ratio'] = df_merged['_idx_vol_ratio'].fillna(1)

    # 清理临时列
    df_merged = df_merged.drop(columns=['_ts_temp', '_idx_pct', '_idx_vol_ratio'],
                               errors='ignore')
    return df_merged


# ============================================================
# 技术指标 (与 v1 保持一致)
# ============================================================

def _rsi(close: pd.Series, period: int) -> pd.Series:
    """RSI = 100 - 100/(1 + RS), RS = EMA(gain) / EMA(loss)"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    """MACD = EMA(12) - EMA(26), Signal = EMA(MACD, 9), Hist = MACD - Signal"""
    ema_fast = close.ewm(span=fast, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def _atr(high, low, close, period):
    """ATR = EMA(TR, alpha=1/period), TR = max(H-L, |H-prevC|, |L-prevC|)"""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


def _bollinger(close, period, std_mult):
    """布林带: mid=MA(period), upper/lower=mid±std*σ, width=(upper-lower)/mid"""
    mid = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return mid, upper, lower, width


def _obv(close, volume):
    """OBV: 累计量, 涨+量, 跌-量"""
    return (volume * np.where(close.diff() > 0, 1,
                              np.where(close.diff() < 0, -1, 0))).cumsum()


def _mfi(high, low, close, volume, period):
    """MFI = 100 - 100/(1 + MR), MR = sum(正向RMF) / sum(负向RMF)"""
    tp = (high + low + close) / 3
    rmf = tp * volume
    direction = np.where(tp.diff() > 0, 1, 0)
    pos_sum = pd.Series(rmf * direction).rolling(period, min_periods=1).sum()
    neg_sum = pd.Series(rmf * (1 - direction)).rolling(period, min_periods=1).sum()
    mr = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - (100 / (1 + mr))


# ============================================================
# 主流程
# ============================================================

def process_stock(stock_code: str, td: TdConnector, sentiment_map: dict) -> int:
    """
    处理单只股票的完整特征计算流程。

    步骤:
    1. 获取 f_{code} 表的最新时间戳 (增量计算)
    2. 读取 m_{code} 原始K线 + 对应 i_{idx_code} 指数K线
    3. 调用 compute_features 计算 29 维特征
    4. 过滤掉已有数据 → 写入 f_{code}

    返回: 写入的行数
    """
    logging.info(f"处理 {stock_code} ...")

    # ── 确定增量起始点 ──
    latest = td.get_latest_ts(f"f_{stock_code}")

    # ── v2.3 增量预热: 多取 200 条K线给滚动/EMA 指标计算 ──
    # 增量运行时仅加载 ts > latest 的行, MA(60)/RSI(14) 等在前60行边界失真。
    # 向前多取 200 条 (约800分钟) 作为 warmup, 后续过滤切掉。
    if latest is not None:
        warmup_start = latest - pd.Timedelta(minutes=800)
        df_stock = td.get_raw_klines(stock_code, start_ts=warmup_start)
    else:
        df_stock = td.get_raw_klines(stock_code, start_ts=None)

    if df_stock.empty:
        logging.info(f"  {stock_code}: 无新原始数据, 跳过")
        return 0

    logging.info(f"  {stock_code}: {len(df_stock)} 条原始K线 "
                 f"({df_stock['ts'].min()} ~ {df_stock['ts'].max()})")

    # ── 读取对应的指数K线 (同样带 warmup) ──
    idx_code = get_index_code(stock_code)
    df_index = td.get_index_klines(idx_code, start_ts=warmup_start if latest else None)
    if df_index.empty:
        logging.warning(f"  {stock_code}: 指数 {idx_code} 无数据, "
                        f"大盘特征将填充为 0")
    else:
        logging.info(f"  {stock_code}: 指数 {idx_code} {len(df_index)} 条")

    # ── 计算 29 维特征 ──
    df_features = compute_features(df_stock, df_index, sentiment_map)

    # ── 二次去重 (滚动计算可能产生了 latest 之前的数据) ──
    if latest is not None:
        df_features['_cmp'] = pd.to_datetime(df_features['ts']).dt.tz_localize(None)
        ref = pd.Timestamp(latest).tz_localize(None)
        df_features = df_features[df_features['_cmp'] > ref]
        df_features = df_features.drop(columns=['_cmp'])

    if df_features.empty:
        logging.info(f"  {stock_code}: 无新特征需要写入")
        return 0

    # ── 写入 TDengine ──
    written = td.insert_features_v2(stock_code, df_features, ALL_COLS_FEATURE_V2)
    logging.info(f"  {stock_code}: 写入 {written} 条特征")
    return written


def main():
    parser = argparse.ArgumentParser(description='特征工程 v2.3 - 32维特征计算 (含大盘+截面排名)')
    parser.add_argument('--config', default=None, help='配置文件路径')
    parser.add_argument('--stock', help='指定单只股票 (如 sh600036)')
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config, "feature_engineering.log")
    disable_system_proxy()

    logging.info("=" * 60)
    logging.info("特征工程 v2.3 - 32维特征计算 (含指数/大盘)")
    logging.info("=" * 60)

    td = TdConnector(config)
    if not td.connect():
        sys.exit(1)

    try:
        # ── 确定股票列表 ──
        if args.stock:
            stocks = [{'代码': args.stock, '名称': args.stock}]
        else:
            stocks = load_stock_list(config['data'].get('stock_list', 'stock_list.csv'))
            if not stocks:
                logging.error("股票列表为空")
                return

        # ── 加载市场情绪数据 ──
        min_date = (pd.Timestamp.now() - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
        sentiment_map = td.get_sentiment_series(min_date)
        logging.info(f"市场情绪: {len(sentiment_map)} 条日数据"
                     if sentiment_map else "市场情绪为空")

        total_written, failed = 0, []
        start_time = time.time()

        for s in stocks:
            code = s.get('代码', s.get('code', ''))
            try:
                total_written += process_stock(code, td, sentiment_map)
            except Exception as e:
                logging.error(f"  {code}: 特征计算失败: {e}")
                failed.append(code)

        elapsed = time.time() - start_time
        logging.info("=" * 60)
        logging.info(f"特征工程完成: 写入 {total_written} 条特征, "
                     f"失败 {len(failed)} 只, 耗时 {elapsed:.1f}s")
        if failed:
            logging.warning(f"失败股票: {', '.join(failed)}")
        logging.info("=" * 60)

        # ══════════════════════════════════════════════════════
        # v2.3: 截面归一化排名 (仅全量模式, --stock 模式跳过)
        # ══════════════════════════════════════════════════════
        if not args.stock:
            logging.info("─" * 40)
            logging.info("启动截面归一化排名 (cross-sectional rank)")
            logging.info("─" * 40)
            from cross_sectional import _compute_ranks_for_day, _merge_and_rebuild

            temp_dir = PROJECT_ROOT / "data" / "cross_sectional_tmp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            for old in temp_dir.glob("*.parquet"):
                old.unlink(missing_ok=True)

            # 获取交易日列表
            dates = sorted(set(
                pd.Timestamp(d).strftime('%Y%m%d')
                for code in stocks
                for d in (_get_date_range(td, code['代码']) if isinstance(code, dict) else [])
                # 简化为: 从 sentiment_map 的日期范围
            ))
            # 用每天的 sentiment key 作为 trading dates
            dates = sorted(sentiment_map.keys())
            if not dates:
                logging.warning("无交易日数据, 跳过截面归一化")
            else:
                t_cs = time.time()
                days_ok = 0
                for date in dates:
                    try:
                        n = _compute_ranks_for_day(td, stocks, date, temp_dir)
                        if n > 0:
                            days_ok += 1
                    except Exception as e:
                        logging.error(f"  排名 {date}: {e}")
                logging.info(f"排名计算: {days_ok}/{len(dates)} 天, "
                             f"耗时 {(time.time()-t_cs)/60:.1f}分钟")

                t_rebuild = time.time()
                for i, s in enumerate(stocks):
                    code = s.get('代码', '')
                    try:
                        _merge_and_rebuild(td, code, temp_dir)
                    except Exception as e:
                        logging.error(f"  重建 {code}: {e}")
                    if (i + 1) % 50 == 0:
                        logging.info(f"  重建: [{i+1}/{len(stocks)}]")
                logging.info(f"重建: {len(stocks)} 只, "
                             f"耗时 {(time.time()-t_rebuild)/60:.1f}分钟")

                # 清理
                for f in temp_dir.glob("*"):
                    f.unlink(missing_ok=True)
                try:
                    temp_dir.rmdir()
                except OSError:
                    pass
                logging.info("截面归一化完成")

    finally:
        td.close()


if __name__ == '__main__':
    main()
