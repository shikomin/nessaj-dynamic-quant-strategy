#!/usr/bin/env python3
"""
特征工程模块
从 TDengine 原始1分钟K线 → 计算26维特征 → 存入 features_1m 表

用法:
  python feature_engineering.py              # 处理 stock_list.csv 中所有股票
  python feature_engineering.py --stock sh600036  # 只处理指定股票
"""

import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import quote_plus
from typing import Optional

import yaml
import numpy as np
import pandas as pd
from utils import disable_system_proxy

try:
    import taosws
except ImportError:
    print("请安装 taos-ws-py: pip install taos-ws-py", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# 配置 & 日志
# ============================================================

def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "config.yaml"
    else:
        config_path = Path(config_path)
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config


def setup_logging(config: dict):
    log_cfg = config.get('logger', {})
    level = getattr(logging, log_cfg.get('level', 'INFO').upper(), logging.INFO)
    log_file = PROJECT_ROOT / "data" / "logs" / "feature_engineering.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(str(log_file), encoding='utf-8'),
            logging.StreamHandler(sys.stdout),
        ]
    )


# ============================================================
# TDengine 连接器 (精简版)
# ============================================================

class TdengineConnector:
    def __init__(self, config: dict):
        td_cfg = config['tdengine']
        self.host = td_cfg['host']
        self.port = td_cfg['port']
        self.database = td_cfg['database']
        self.username = td_cfg.get('username', 'root')
        self.password = td_cfg.get('password', 'taosdata')
        self._conn = None

    @property
    def conn(self):
        assert self._conn is not None, "TDengine 未连接"
        return self._conn

    def connect(self) -> bool:
        from urllib.parse import quote_plus
        try:
            dsn = f"taosws://{quote_plus(self.username)}:{quote_plus(self.password)}@{self.host}:{self.port}"
            self._conn = taosws.connect(dsn)
            self._conn.execute(f"USE {self.database}")
            logging.info(f"已连接 TDengine: {self.host}:{self.port}/{self.database}")
            return True
        except Exception as e:
            logging.error(f"连接 TDengine 失败: {e}")
            return False

    def _fetch_rows(self, sql: str) -> list:
        """执行 SELECT 并返回 list[tuple]"""
        result = self._conn.query(sql)
        return list(result)

    def ensure_feature_subtable(self, stock_code: str):
        table = f"f_{stock_code}"
        market = "SZ" if stock_code.lower().startswith("sz") else "SH"
        try:
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "
                f"USING features_1m TAGS ('{stock_code}', '{market}')"
            )
        except Exception as e:
            logging.error(f"创建特征子表失败 {table}: {e}")
            raise

    def get_raw_klines(self, stock_code: str, start_ts: Optional[datetime] = None) -> pd.DataFrame:
        table = f"m_{stock_code}"
        self.ensure_feature_subtable(stock_code)

        sql = f"SELECT ts, open, high, low, close, volume FROM {table}"
        if start_ts is not None:
            ts_str = start_ts.strftime('%Y-%m-%d %H:%M:%S')
            sql += f" WHERE ts > '{ts_str}'"
        sql += " ORDER BY ts ASC"

        try:
            rows = self._fetch_rows(sql)
            if not rows:
                return pd.DataFrame(columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
            df = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
            df['ts'] = pd.to_datetime(df['ts'])
            for c in ['open', 'high', 'low', 'close', 'volume']:
                df[c] = pd.to_numeric(df[c], errors='coerce')
            return df
        except Exception as e:
            logging.error(f"  查询 {table} 失败: {e}")
            return pd.DataFrame(columns=['ts', 'open', 'high', 'low', 'close', 'volume'])

    def get_latest_feature_ts(self, stock_code: str) -> Optional[datetime]:
        table = f"f_{stock_code}"
        try:
            rows = self._fetch_rows(f"SELECT ts FROM {table} ORDER BY ts DESC LIMIT 1")
            if rows and rows[0] and rows[0][0] is not None:
                return pd.Timestamp(rows[0][0])
        except Exception:
            pass
        return None

    def get_sentiment_series(self, start_date: str) -> dict:
        """start_date 格式: '2025-06-01' """
        try:
            sql = (
                f"SELECT ts, close FROM sent_daily "
                f"WHERE ts >= '{start_date} 00:00:00' ORDER BY ts ASC"
            )
            rows = self._fetch_rows(sql)
            sentiment_map = {}
            for row in rows:
                if row[0] and row[1] is not None:
                    ts = pd.to_datetime(row[0])
                    date_key = ts.strftime('%Y%m%d')
                    sentiment_map[date_key] = float(row[1])
            return sentiment_map
        except Exception as e:
            logging.warning(f"  获取市场情绪失败: {e}")
            return {}

    def insert_features(self, stock_code: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0

        table = f"f_{stock_code}"
        col_names = [
            'open', 'high', 'low', 'close', 'volume',
            'volume_ma5', 'volume_ratio', 'ma5', 'ma10', 'ma20', 'ma60',
            'rsi_6', 'rsi_14', 'macd', 'macd_signal', 'macd_hist',
            'atr_14', 'bb_upper', 'bb_lower', 'bb_width',
            'obv', 'mfi_14', 'amplitude_pct', 'volume_momentum',
            'price_position', 'sentiment'
        ]

        total = 0
        batch_size = 300
        for start in range(0, len(df), batch_size):
            batch = df.iloc[start:start + batch_size]
            values_list = []
            for _, row in batch.iterrows():
                ts = row['ts']
                ts_str = ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if isinstance(ts, (datetime, pd.Timestamp)) else str(ts)
                vals = [f"'{ts_str}'"]
                for c in col_names:
                    v = row.get(c, np.nan)
                    if pd.isna(v) or np.isinf(v):
                        vals.append("NULL")
                    else:
                        vals.append(str(round(float(v), 6)))
                values_list.append("(" + ", ".join(vals) + ")")

            if values_list:
                all_cols = "ts, " + ", ".join(col_names)
                sql = f"INSERT INTO {table} ({all_cols}) VALUES " + " ".join(values_list)
                try:
                    self._conn.execute(sql)
                    total += len(batch)
                except Exception as e:
                    logging.error(f"  {table} 批量写入失败 [{start}:{start+len(batch)}]: {e}")
                    for i, val in enumerate(values_list):
                        try:
                            self._conn.execute(f"INSERT INTO {table} ({all_cols}) VALUES {val}")
                            total += 1
                        except Exception as e2:
                            logging.error(f"    单条写入失败 [{start+i}]: {e2}")
        return total

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ============================================================
# 特征计算 (纯 Pandas/NumPy，无需 TA-Lib)
# ============================================================

def compute_features(df: pd.DataFrame, sentiment_map: dict) -> pd.DataFrame:
    """
    输入: OHLCV DataFrame (ts, open, high, low, close, volume)
    输出: 26 维特征 DataFrame
    """
    if df.empty:
        return df

    df = df.copy()
    open_ = df['open']
    high = df['high']
    low = df['low']
    close = df['close']
    volume = df['volume'].astype(float)

    # ── 成交量特征 (volume_ma5, volume_ratio) ──
    df['volume_ma5'] = volume.rolling(5, min_periods=1).mean()
    df['volume_ratio'] = volume / df['volume_ma5'].replace(0, np.nan)

    # ── 趋势特征 (MA) ──
    for p in [5, 10, 20, 60]:
        col = f'ma{p}'
        df[col] = close.rolling(p, min_periods=1).mean()

    # ── RSI ──
    df['rsi_6'] = _compute_rsi(close, 6)
    df['rsi_14'] = _compute_rsi(close, 14)

    # ── MACD ──
    macd_line, signal_line, hist_line = _compute_macd(close)
    df['macd'] = macd_line
    df['macd_signal'] = signal_line
    df['macd_hist'] = hist_line

    # ── ATR ──
    df['atr_14'] = _compute_atr(high, low, close, 14)

    # ── 布林带 ──
    bb_mid, bb_upper, bb_lower, bb_width = _compute_bollinger(close, 20, 2)
    df['bb_upper'] = bb_upper
    df['bb_lower'] = bb_lower
    df['bb_width'] = bb_width

    # ── OBV ──
    df['obv'] = _compute_obv(close, volume)

    # ── MFI ──
    df['mfi_14'] = _compute_mfi(high, low, close, volume, 14)

    # ── 振幅 ──
    df['amplitude_pct'] = (high - low) / close.replace(0, np.nan) * 100

    # ── 量能动量 (volume / volume_ma20) ──
    vol_ma20 = volume.rolling(20, min_periods=1).mean()
    df['volume_momentum'] = volume / vol_ma20.replace(0, np.nan)

    # ── 价格位置 (close 在日内高低区间的位置 0~1) ──
    h_l_range = (high - low).replace(0, np.nan)
    df['price_position'] = (close - low) / h_l_range

    # ── 市场情绪 (按日期广播) ──
    df['date_key'] = df['ts'].dt.strftime('%Y%m%d')
    df['sentiment'] = df['date_key'].map(sentiment_map).fillna(0)
    df.drop(columns=['date_key'], inplace=True)

    # ── 前向填充 NaN ──
    cols_to_fill = [
        'volume_ma5', 'volume_ratio', 'ma5', 'ma10', 'ma20', 'ma60',
        'rsi_6', 'rsi_14', 'macd', 'macd_signal', 'macd_hist',
        'atr_14', 'bb_upper', 'bb_lower', 'bb_width',
        'obv', 'mfi_14', 'amplitude_pct', 'volume_momentum',
        'price_position', 'sentiment'
    ]
    df[cols_to_fill] = df[cols_to_fill].ffill().fillna(0)

    return df


def _compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1/period, min_periods=period).mean()


def _compute_bollinger(close: pd.Series, period: int, std_mult: float):
    mid = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return mid, upper, lower, width


def _compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.where(close.diff() > 0, 1, np.where(close.diff() < 0, -1, 0))
    return (volume * direction).cumsum()


def _compute_mfi(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series, period: int) -> pd.Series:
    typical_price = (high + low + close) / 3
    raw_money_flow = typical_price * volume
    direction = np.where(typical_price.diff() > 0, 1, 0)
    positive_flow = raw_money_flow * direction
    negative_flow = raw_money_flow * (1 - direction)

    pos_sum = pd.Series(positive_flow).rolling(period, min_periods=1).sum()
    neg_sum = pd.Series(negative_flow).rolling(period, min_periods=1).sum()
    money_ratio = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - (100 / (1 + money_ratio))


# ============================================================
# 主流程
# ============================================================

def load_stock_list(csv_path: str) -> list[dict]:
    path = Path(csv_path)
    if not path.exists():
        logging.error(f"股票列表文件不存在: {path}")
        return []
    df = pd.read_csv(path, dtype=str)
    return [{k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items()}
            for _, row in df.iterrows()]


def process_stock(code: str, td: TdengineConnector, sentiment_map: dict):
    logging.info(f"处理 {code} ...")

    # 增量：只计算特征表中尚不存在的分钟线
    latest_feature_ts = td.get_latest_feature_ts(code)
    df = td.get_raw_klines(code, start_ts=latest_feature_ts)
    if df.empty:
        logging.info(f"  {code}: 无新原始数据，跳过")
        return 0

    ts_min = df['ts'].min()
    ts_max = df['ts'].max()
    logging.info(f"  {code}: {len(df)} 条原始K线 ({ts_min} ~ {ts_max})")

    # 计算特征
    df_features = compute_features(df, sentiment_map)

    # 去除已有特征行（按 ts 过滤）
    if latest_feature_ts is not None:
        df_features['_ts_cmp'] = pd.to_datetime(df_features['ts']).dt.tz_localize(None)
        ref = pd.Timestamp(latest_feature_ts).tz_localize(None)
        df_features = df_features[df_features['_ts_cmp'] > ref]
        df_features = df_features.drop(columns=['_ts_cmp'])

    if df_features.empty:
        logging.info(f"  {code}: 无新特征需要写入")
        return 0

    written = td.insert_features(code, df_features)
    logging.info(f"  {code}: 写入 {written} 条特征")
    return written


def main():
    parser = argparse.ArgumentParser(description='特征工程 - 26维特征计算')
    parser.add_argument('--config', default=None, help='配置文件路径')
    parser.add_argument('--stock', help='指定单只股票')
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)
    disable_system_proxy()

    logging.info("=" * 60)
    logging.info("特征工程 - 26维特征计算")
    logging.info("=" * 60)

    td = TdengineConnector(config)
    if not td.connect():
        sys.exit(1)

    try:
        stock_list_file = config['data'].get('stock_list', 'stock_list.csv')
        csv_path = PROJECT_ROOT / stock_list_file
        if args.stock:
            stocks = [{'代码': args.stock, '名称': args.stock}]
        else:
            stocks = load_stock_list(csv_path)
            if not stocks:
                logging.error("股票列表为空")
                return

        latest = td.get_latest_feature_ts(stocks[0].get('代码', '')) if stocks else None
        min_date = (datetime.now() - pd.Timedelta(days=365)).strftime('%Y-%m-%d')
        sentiment_map = td.get_sentiment_series(min_date)
        if sentiment_map:
            logging.info(f"市场情绪: {len(sentiment_map)} 条日数据")
        else:
            logging.warning("市场情绪数据为空 (sent_daily 表可能无数据，请先运行 data_fetcher.py)")

        total_written = 0
        failed = []
        start_time = time.time()

        for i, s in enumerate(stocks):
            code = s.get('代码', s.get('code', ''))
            try:
                written = process_stock(code, td, sentiment_map)
                total_written += written
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
