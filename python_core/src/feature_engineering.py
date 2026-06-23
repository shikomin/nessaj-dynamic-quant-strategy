#!/usr/bin/env python3
"""
特征工程模块
从 TDengine 原始1分钟K线 → 计算26维特征 → 存入 features_1m 表
"""
import sys
import time
import logging
import argparse

import numpy as np
import pandas as pd

from config import load_config, PROJECT_ROOT
from logger import setup_logging
from td_connector import TdConnector
from utils import disable_system_proxy


# ============================================================
# 特征计算 (纯 Pandas/NumPy)
# ============================================================

def compute_features(df: pd.DataFrame, sentiment_map: dict) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()
    open_ = df['open']
    high = df['high']
    low = df['low']
    close = df['close']
    volume = df['volume'].astype(float)

    # ── 成交量 ──
    df['volume_ma5'] = volume.rolling(5, min_periods=1).mean()
    df['volume_ratio'] = volume / df['volume_ma5'].replace(0, np.nan)

    # ── 趋势 MA ──
    for p in [5, 10, 20, 60]:
        df[f'ma{p}'] = close.rolling(p, min_periods=1).mean()

    # ── RSI ──
    df['rsi_6'] = _rsi(close, 6)
    df['rsi_14'] = _rsi(close, 14)

    # ── MACD ──
    df['macd'], df['macd_signal'], df['macd_hist'] = _macd(close)

    # ── ATR ──
    df['atr_14'] = _atr(high, low, close, 14)

    # ── 布林带 ──
    _, u, l, w = _bollinger(close, 20, 2)
    df['bb_upper'] = u
    df['bb_lower'] = l
    df['bb_width'] = w

    # ── OBV ──
    df['obv'] = _obv(close, volume)

    # ── MFI ──
    df['mfi_14'] = _mfi(high, low, close, volume, 14)

    # ── 振幅 ──
    df['amplitude_pct'] = (high - low) / close.replace(0, np.nan) * 100

    # ── 量能动量 ──
    vol_ma20 = volume.rolling(20, min_periods=1).mean()
    df['volume_momentum'] = volume / vol_ma20.replace(0, np.nan)

    # ── 价格位置 ──
    h_l_range = (high - low).replace(0, np.nan)
    df['price_position'] = (close - low) / h_l_range

    # ── 市场情绪 ──
    df['date_key'] = df['ts'].dt.strftime('%Y%m%d')
    df['sentiment'] = df['date_key'].map(sentiment_map).fillna(0)
    df.drop(columns=['date_key'], inplace=True)

    # ── 前向填充 NaN ──
    fill_cols = [c for c in df.columns if c not in ('ts', 'open', 'high', 'low', 'close', 'volume')]
    df[fill_cols] = df[fill_cols].ffill().fillna(0)

    return df


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal).mean()
    return macd_line, signal_line, macd_line - signal_line


def _atr(high, low, close, period):
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


def _bollinger(close, period, std_mult):
    mid = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return mid, upper, lower, width


def _obv(close, volume):
    return (volume * np.where(close.diff() > 0, 1, np.where(close.diff() < 0, -1, 0))).cumsum()


def _mfi(high, low, close, volume, period):
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

def load_stock_list(csv_path: str) -> list[dict]:
    path = PROJECT_ROOT / csv_path
    if not path.exists():
        logging.error(f"股票列表文件不存在: {path}")
        return []
    df = pd.read_csv(path, dtype=str)
    return [{k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items()}
            for _, row in df.iterrows()]


def process_stock(code: str, td: TdConnector, sentiment_map: dict):
    logging.info(f"处理 {code} ...")
    latest = td.get_latest_ts(f"f_{code}")
    df = td.get_raw_klines(code, start_ts=latest)
    if df.empty:
        logging.info(f"  {code}: 无新原始数据，跳过")
        return 0

    logging.info(f"  {code}: {len(df)} 条原始K线 ({df['ts'].min()} ~ {df['ts'].max()})")
    df_features = compute_features(df, sentiment_map)

    if latest is not None:
        df_features['_cmp'] = pd.to_datetime(df_features['ts']).dt.tz_localize(None)
        ref = pd.Timestamp(latest).tz_localize(None)
        df_features = df_features[df_features['_cmp'] > ref]
        df_features = df_features.drop(columns=['_cmp'])

    if df_features.empty:
        logging.info(f"  {code}: 无新特征需要写入")
        return 0

    written = td.insert_features(code, df_features)
    logging.info(f"  {code}: 写入 {written} 条特征")
    return written


def main():
    parser = argparse.ArgumentParser(description='特征工程 - 26维特征计算')
    parser.add_argument('--config', default=None)
    parser.add_argument('--stock')
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config, "feature_engineering.log")
    disable_system_proxy()

    logging.info("=" * 60)
    logging.info("特征工程 - 26维特征计算")
    logging.info("=" * 60)

    td = TdConnector(config)
    if not td.connect():
        sys.exit(1)

    try:
        if args.stock:
            stocks = [{'代码': args.stock, '名称': args.stock}]
        else:
            stocks = load_stock_list(config['data'].get('stock_list', 'stock_list.csv'))
            if not stocks:
                logging.error("股票列表为空")
                return

        min_date = (pd.Timestamp.now() - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
        sentiment_map = td.get_sentiment_series(min_date)
        logging.info(f"市场情绪: {len(sentiment_map)} 条日数据" if sentiment_map else "市场情绪为空")

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
        logging.info(f"特征工程完成: 写入 {total_written} 条, 失败 {len(failed)}, 耗时 {elapsed:.1f}s")
        if failed:
            logging.warning(f"失败: {', '.join(failed)}")
        logging.info("=" * 60)

    finally:
        td.close()


if __name__ == '__main__':
    main()
