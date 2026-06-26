"""
TDengine 统一连接器
===================

所有 Python 脚本通过此类与 TDengine 3.x 交互。
使用 taos-ws-py 的 WebSocket 连接方式, 支持远程访问。

===================================================================
表结构约定
-----------
原始K线超级表:
  kline_daily → 子表 d_{code}  (日线)
  kline_1m   → 子表 m_{code}  (1分钟线)

特征超级表:
  features_1m → 子表 f_{code}  (29维分钟特征, v2.2含大盘特征)

指数超级表:
  index_kline_1m → 子表 i_{code}  (指数1分钟K线, v2.2新增)

情绪超级表:
  market_sentiment → 子表 sent_daily / sent_hot / sent_trend

===================================================================
taos-ws-py 0.6.x 兼容性
-------------------------
- connect() 返回 TaosConnection
- query()   返回 TaosResult, 用 list() 取值
- ⚠ MAX(ts) 不可靠: 用 ORDER BY ts DESC LIMIT 1 代替
- ⚠ fetch_all() 不存在: 直接用 list(result) 取值

===================================================================
ALL_COLS_FEATURE 定义
----------------------
26维特征列名, 顺序与 TDengine 表列顺序一致:
  open, high, low, close, volume           (5维: 价格基础 + 成交量)
  volume_ma5, volume_ratio                 (2维: 成交量衍生)
  ma5, ma10, ma20, ma60                    (4维: 趋势)
  rsi_6, rsi_14, macd, macd_signal, macd_hist (5维: 动量)
  atr_14, bb_upper, bb_lower, bb_width     (4维: 波动)
  obv, mfi_14                              (2维: 量价)
  amplitude_pct, volume_momentum, price_position (3维: 市场结构)
  sentiment                                (1维: 市场情绪)
"""
import logging
from datetime import datetime
from urllib.parse import quote_plus
from typing import Optional

import numpy as np
import pandas as pd

from components.config import PROJECT_ROOT

try:
    import taosws
except ImportError:
    raise ImportError("请安装 taos-ws-py: pip install taos-ws-py")

# ============================================================
# 列定义常量 (所有脚本共享)
# ============================================================

# 原始K线表的 7 列 (日线和分钟线通用)
ALL_COLS_KLINE = ['ts', 'open', 'high', 'low', 'close', 'volume', 'amount']

# 原始分钟K线查询列 (回测只需要 OHLCV, 不需要 amount)
ALL_COLS_M_QUERY = ['ts', 'open', 'high', 'low', 'close', 'volume']

# 26 维特征列 (v1, 已废弃, 保留向后兼容)
ALL_COLS_FEATURE = [
    'open', 'high', 'low', 'close', 'volume',
    'volume_ma5', 'volume_ratio', 'ma5', 'ma10', 'ma20', 'ma60',
    'rsi_6', 'rsi_14', 'macd', 'macd_signal', 'macd_hist',
    'atr_14', 'bb_upper', 'bb_lower', 'bb_width',
    'obv', 'mfi_14', 'amplitude_pct', 'volume_momentum',
    'price_position', 'sentiment'
]

# 32 维特征列 v2.3 (与 features_1m 超级表列顺序一致)
ALL_COLS_FEATURE_V2 = [
    'open', 'high', 'low', 'close',
    'volume', 'volume_ma5', 'volume_ratio',
    'ma5', 'ma10', 'ma20', 'ma60',
    'rsi_6', 'rsi_14', 'macd', 'macd_signal', 'macd_hist',
    'atr_14', 'bb_upper', 'bb_lower', 'bb_width',
    'obv', 'mfi_14',
    'amplitude_pct', 'volume_momentum', 'price_position',
    'sentiment',
    'index_change_pct', 'relative_strength', 'index_volume_ratio',
    'rank_ma5', 'rank_rsi_14', 'rank_volume',
]


class TdConnector:
    """
    TDengine 3.x WebSocket 统一连接器 (兼容 taos-ws-py 0.6.x)。

    用法:
        td = TdConnector(config)
        if td.connect():
            rows = td.query("SELECT ts, close FROM m_sh600036 WHERE ts > '2025-06-01'")
            td.close()
    """

    def __init__(self, config: dict):
        """
        初始化连接参数。

        从 config.yaml 的 tdengine 节读取 host, port, database, username, password。
        密码可以从环境变量 TDENGINE_PASSWORD 覆盖。
        """
        cfg = config['tdengine']
        self.host = cfg['host']
        self.port = cfg['port']
        self.database = cfg['database']
        self.username = cfg.get('username', 'root')
        self.password = cfg.get('password', 'taosdata')
        self._conn = None

    # ── 连接管理 ──

    def connect(self) -> bool:
        """
        建立 TDengine WebSocket 连接。

        连接 DSN 格式: taosws://user:password@host:port
        连接成功后执行 USE database 切换到目标库。
        """
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
        """关闭连接, 释放资源。"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logging.info("TDengine 连接已关闭")

    # ── 查询接口 ──

    def query(self, sql: str) -> list:
        """
        执行 SELECT 查询, 返回 list[tuple]。

        taos-ws-py 0.6.x 的 query() 返回 TaosResult 对象,
        迭代 TaosResult 获取每行 tuple。
        """
        return list(self._conn.query(sql))

    def execute(self, sql: str):
        """
        执行 DDL / DML 语句 (CREATE, INSERT, DROP 等)。

        不返回结果集。
        """
        self._conn.execute(sql)

    # ── 子表管理 ──
    # TDengine 使用超级表(STable) + 子表(Subtable)模型。
    # 超级表定义列结构和标签, 子表通过 USING 和 TAGS 继承。

    def ensure_kline_subtable(self, stock_code: str, table_type: str):
        """
        确保 K 线子表存在 (不存在则创建)。

        子表命名规则:
          - 日线: d_{code} (如 d_sh600036)
          - 分钟线: m_{code} (如 m_sh600036)

        标签: (stock_code, market), market 从代码前缀推断 (SH/SZ)。
        """
        stable = "kline_daily" if table_type == "daily" else "kline_1m"
        table = f"d_{stock_code}" if table_type == "daily" else f"m_{stock_code}"
        market = self._market(stock_code)
        self.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"USING {stable} TAGS ('{stock_code}', '{market}')"
        )

    def ensure_feature_subtable(self, stock_code: str):
        """
        确保特征子表存在。

        子表命名规则: f_{code} (如 f_sh600036)
        使用 features_1m 超级表, 标签与 K 线表一致。
        """
        table = f"f_{stock_code}"
        market = self._market(stock_code)
        self.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"USING features_1m TAGS ('{stock_code}', '{market}')"
        )

    def ensure_sentiment_subtable(self, table_name: str, model: str):
        """
        确保情绪子表存在。

        market_sentiment 超级表的子表:
          - sent_daily: model='market_sentiment'
          - sent_hot:   model='market_hot_sentiment'
          - sent_trend: model='sentiment_trend'
        """
        self.execute(
            f"CREATE TABLE IF NOT EXISTS {table_name} "
            f"USING market_sentiment TAGS ('{model}')"
        )

    def ensure_index_subtable(self, index_code: str):
        """
        确保指数子表存在。表名规范: i_{code} (如 i_sh000001)
        """
        table = f"i_{index_code}"
        self.execute(
            f"CREATE TABLE IF NOT EXISTS {table} "
            f"USING index_kline_1m TAGS ('{index_code}', '')"
        )

    @staticmethod
    def _market(code: str) -> str:
        """
        从内部代码推断交易所。

        sh开头 → SH (上海), sz开头 → SZ (深圳)
        """
        return "SZ" if code.lower().startswith("sz") else "SH"

    # ── 时间戳查询 ──

    def get_latest_ts(self, table_name: str) -> Optional[datetime]:
        """
        获取表中最新时间戳, 用于增量更新判断。

        用 ORDER BY ts DESC LIMIT 1 代替不兼容的 MAX(ts) 语法。
        返回 None 表示表为空或查询失败。
        """
        try:
            rows = self.query(f"SELECT ts FROM {table_name} ORDER BY ts DESC LIMIT 1")
            if rows and rows[0] and rows[0][0] is not None:
                return pd.Timestamp(rows[0][0])
        except Exception:
            pass
        return None

    # ── K 线写入 (带去重) ──

    def insert_kline(self, stock_code: str, table_type: str, df: pd.DataFrame) -> int:
        """
        写入 K 线数据, 自动去重 (跳过早于最新时间戳的行)。

        流程:
        1. 确保子表存在
        2. 查询子表最新 ts
        3. 过滤掉 df 中 <= latest_ts 的行
        4. 批量写入剩余行 (每批 300 条)
        """
        if df.empty:
            return 0

        table = f"d_{stock_code}" if table_type == "daily" else f"m_{stock_code}"
        self.ensure_kline_subtable(stock_code, table_type)

        # ── 增量去重 ──
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
        """
        写入 26 维特征数据。

        逻辑与 insert_kline 相同, 但写入 features_1m 超级表的子表。
        列顺序: ['ts'] + ALL_COLS_FEATURE
        """
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

    def insert_features_v2(self, stock_code: str, df: pd.DataFrame,
                           feature_cols: list = None) -> int:
        """
        写入 v2.2 29 维特征数据。

        与 insert_features 的区别: 使用传入的 feature_cols (而非硬编码),
        支持自定义特征列顺序。

        参数
        ----
        stock_code   : 内部代码
        df           : 包含特征列的 DataFrame (含 ts)
        feature_cols : 特征列名列表, 默认为 ALL_COLS_FEATURE_V2
        """
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

        cols = feature_cols if feature_cols else ALL_COLS_FEATURE_V2
        # 只取存在的列
        write_cols = [c for c in cols if c in df.columns]
        return self._insert_rows(table, df, write_cols)

    # ── 原始 K 线读取 ──

    def get_raw_klines(self, stock_code: str, start_ts: Optional[datetime] = None) -> pd.DataFrame:
        """
        读取原始 1分钟K 线数据。

        参数
        ----
        stock_code: 内部代码 (如 sh600036)
        start_ts  : 起始时间戳 (None = 全量读取)

        返回
        ----
        DataFrame: [ts, open, high, low, close, volume], 按时间升序

        用于特征工程模块获取原始数据。
        """
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

    # ── 指数 K 线读取 (v2.2) ──

    def get_index_klines(self, index_code: str, start_ts: Optional[datetime] = None) -> pd.DataFrame:
        """
        读取指数 1分钟K 线数据。

        参数
        ----
        index_code: 指数内部代码 (如 sh000001)
        start_ts  : 起始时间戳 (None = 全量)

        返回
        ----
        DataFrame: [ts, open, high, low, close, volume], 按时间升序
        """
        table = f"i_{index_code}"
        self.ensure_index_subtable(index_code)

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
        """
        获取市场情绪时间序列。

        参数
        ----
        start_date: 起始日期字符串 (如 '2025-06-01')

        返回
        ----
        dict: {日期(YYYYMMDD): 情绪收盘值(float)}
              用于特征工程中按日期广播注入。

        情绪值来自 sent_daily 表的 close 字段 (情绪K线的收盘价)。
        """
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
        """
        批量 INSERT 写入, 带降级逐行重试。

        策略:
        1. 每 300 行一批, 拼成单条多值 INSERT 语句
        2. 如果批量 INSERT 失败, 降级为逐行 INSERT (跳过失败行)
        3. NaN/Inf 值转为 NULL

        返回: 成功写入的行数
        """
        total = 0
        batch_size = 300   # 过大可能导致 SQL 语句超长
        for start in range(0, len(df), batch_size):
            batch = df.iloc[start:start + batch_size]

            # 构造 VALUES 子句
            values_list = []
            for _, row in batch.iterrows():
                ts = row['ts']
                # 时间戳格式: 'YYYY-MM-DD HH:MM:SS.fff' (毫秒精度)
                ts_str = (ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                          if isinstance(ts, (datetime, pd.Timestamp)) else str(ts))
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
                # 列列表: ts + 所有非ts列
                all_cols = "ts, " + ", ".join(c for c in col_order if c != 'ts')
                sql = f"INSERT INTO {table} ({all_cols}) VALUES " + " ".join(values_list)
                try:
                    self.execute(sql)
                    total += len(batch)
                except Exception:
                    # 批量写入失败 → 逐行降级重试
                    for v in values_list:
                        try:
                            self.execute(f"INSERT INTO {table} ({all_cols}) VALUES {v}")
                            total += 1
                        except Exception:
                            pass   # 单行失败静默跳过
        return total
