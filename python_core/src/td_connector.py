"""TDengine 统一连接器 — 所有脚本共用"""
import logging
from datetime import datetime
from urllib.parse import quote_plus
from typing import Optional

import numpy as np
import pandas as pd

from config import PROJECT_ROOT

try:
    import taosws
except ImportError:
    raise ImportError("请安装 taos-ws-py: pip install taos-ws-py")

ALL_COLS_KLINE = ['ts', 'open', 'high', 'low', 'close', 'volume', 'amount']
ALL_COLS_M_QUERY = ['ts', 'open', 'high', 'low', 'close', 'volume']  # m_{code} 表查询列

ALL_COLS_FEATURE = [
    'open', 'high', 'low', 'close', 'volume',
    'volume_ma5', 'volume_ratio', 'ma5', 'ma10', 'ma20', 'ma60',
    'rsi_6', 'rsi_14', 'macd', 'macd_signal', 'macd_hist',
    'atr_14', 'bb_upper', 'bb_lower', 'bb_width',
    'obv', 'mfi_14', 'amplitude_pct', 'volume_momentum',
    'price_position', 'sentiment'
]


class TdConnector:
    """TDengine 3.x WebSocket 统一连接器 (taos-ws-py 0.6.x compatible)"""

    def __init__(self, config: dict):
        cfg = config['tdengine']
        self.host = cfg['host']
        self.port = cfg['port']
        self.database = cfg['database']
        self.username = cfg.get('username', 'root')
        self.password = cfg.get('password', 'taosdata')
        self._conn = None

    # ── 连接管理 ──

    def connect(self) -> bool:
        try:
            dsn = (
                f"taosws://{quote_plus(self.username)}:"
                f"{quote_plus(self.password)}@{self.host}:{self.port}"
            )
            self._conn = taosws.connect(dsn)
            self._conn.execute(f"USE {self.database}")
            logging.info(f"已连接 TDengine: {self.host}:{self.port}/{self.database}")
            return True
        except Exception as e:
            logging.error(f"TDengine 连接失败: {e}")
            return False

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
            logging.info("TDengine 连接已关闭")

    # ── 低级查询 ──

    def query(self, sql: str) -> list:
        """SELECT → list[tuple]。taos-ws-py 0.6.x: query 返回 TaosResult，用 list() 取值"""
        return list(self._conn.query(sql))

    def execute(self, sql: str):
        """DDL / DML"""
        self._conn.execute(sql)

    # ── 子表管理 ──

    def ensure_kline_subtable(self, stock_code: str, table_type: str):
        stable = "kline_daily" if table_type == "daily" else "kline_1m"
        table = f"d_{stock_code}" if table_type == "daily" else f"m_{stock_code}"
        market = self._market(stock_code)
        self.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"USING {stable} TAGS ('{stock_code}', '{market}')"
        )

    def ensure_feature_subtable(self, stock_code: str):
        table = f"f_{stock_code}"
        market = self._market(stock_code)
        self.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"USING features_1m TAGS ('{stock_code}', '{market}')"
        )

    def ensure_sentiment_subtable(self, table_name: str, model: str):
        self.execute(
            f"CREATE TABLE IF NOT EXISTS {table_name} "
            f"USING market_sentiment TAGS ('{model}')"
        )

    @staticmethod
    def _market(code: str) -> str:
        return "SZ" if code.lower().startswith("sz") else "SH"

    # ── 时间戳查询 ──

    def get_latest_ts(self, table_name: str) -> Optional[datetime]:
        try:
            rows = self.query(f"SELECT ts FROM {table_name} ORDER BY ts DESC LIMIT 1")
            if rows and rows[0] and rows[0][0] is not None:
                return pd.Timestamp(rows[0][0])
        except Exception:
            pass
        return None

    # ── K线写入 (带去重) ──

    def insert_kline(self, stock_code: str, table_type: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0

        table = f"d_{stock_code}" if table_type == "daily" else f"m_{stock_code}"
        self.ensure_kline_subtable(stock_code, table_type)

        latest = self.get_latest_ts(table)
        if latest is not None:
            before = len(df)
            df['_cmp'] = pd.to_datetime(df['ts']).dt.tz_localize(None)
            ref = pd.Timestamp(latest).tz_localize(None)
            df = df[df['_cmp'] > ref]
            df = df.drop(columns=['_cmp'])
            skipped = before - len(df)
            if skipped > 0:
                logging.info(f"  {table}: 跳过 {skipped} 条已有")

        if df.empty:
            return 0

        return self._insert_rows(table, df, ALL_COLS_KLINE)

    # ── 特征写入 ──

    def insert_features(self, stock_code: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        table = f"f_{stock_code}"
        self.ensure_feature_subtable(stock_code)

        latest = self.get_latest_ts(table)
        if latest is not None:
            df['_cmp'] = pd.to_datetime(df['ts']).dt.tz_localize(None)
            ref = pd.Timestamp(latest).tz_localize(None)
            df = df[df['_cmp'] > ref]
            df = df.drop(columns=['_cmp'])

        if df.empty:
            return 0

        return self._insert_rows(table, df, ALL_COLS_FEATURE)

    # ── 原始K线读取 ──

    def get_raw_klines(self, stock_code: str, start_ts: Optional[datetime] = None) -> pd.DataFrame:
        table = f"m_{stock_code}"
        self.ensure_kline_subtable(stock_code, '1m')

        sql = f"SELECT ts, open, high, low, close, volume FROM {table}"
        if start_ts is not None:
            sql += f" WHERE ts > '{start_ts.strftime('%Y-%m-%d %H:%M:%S')}'"
        sql += " ORDER BY ts ASC"

        rows = self.query(sql)
        if not rows:
            return pd.DataFrame(columns=ALL_COLS_M_QUERY)

        df = pd.DataFrame(rows, columns=ALL_COLS_M_QUERY)
        df['ts'] = pd.to_datetime(df['ts'])
        for c in ['open', 'high', 'low', 'close', 'volume']:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        return df

    # ── 市场情绪 ──

    def get_sentiment_series(self, start_date: str) -> dict:
        """start_date: '2025-06-01', 返回 {date_str: close_value}"""
        try:
            sql = (
                f"SELECT ts, close FROM sent_daily "
                f"WHERE ts >= '{start_date} 00:00:00' ORDER BY ts ASC"
            )
            rows = self.query(sql)
            return {
                pd.to_datetime(r[0]).strftime('%Y%m%d'): float(r[1])
                for r in rows if r[0] and r[1] is not None
            }
        except Exception as e:
            logging.warning(f"获取市场情绪失败: {e}")
            return {}

    # ── 内部批量写入 ──

    def _insert_rows(self, table: str, df: pd.DataFrame, col_order: list) -> int:
        total = 0
        batch_size = 300
        for start in range(0, len(df), batch_size):
            batch = df.iloc[start:start + batch_size]
            values_list = []
            for _, row in batch.iterrows():
                ts = row['ts']
                ts_str = ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if isinstance(ts, (datetime, pd.Timestamp)) else str(ts)
                vals = [f"'{ts_str}'"]
                for c in col_order:
                    if c == 'ts':
                        continue
                    v = row.get(c, np.nan)
                    if pd.isna(v) or np.isinf(v):
                        vals.append("NULL")
                    else:
                        vals.append(str(round(float(v), 6)))
                values_list.append("(" + ", ".join(vals) + ")")

            if values_list:
                all_cols = "ts, " + ", ".join(c for c in col_order if c != 'ts')
                sql = f"INSERT INTO {table} ({all_cols}) VALUES " + " ".join(values_list)
                try:
                    self.execute(sql)
                    total += len(batch)
                except Exception:
                    for v in values_list:
                        try:
                            self.execute(f"INSERT INTO {table} ({all_cols}) VALUES {v}")
                            total += 1
                        except Exception:
                            pass
        return total
