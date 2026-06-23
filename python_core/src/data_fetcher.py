#!/usr/bin/env python3
"""
A股动态参数量化交易系统 - 数据采集模块
支持 zzshare (自在量化) / AkShare 双数据源

用法:
  python data_fetcher.py                        # 批量采集 stock_list.csv (默认 zzshare 1分钟线)
  python data_fetcher.py --daily                # 采集日线数据
  python data_fetcher.py --source akshare       # 切换为 AkShare 采集
  python data_fetcher.py --stock sh600036       # 只采集指定股票
"""

import os
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote_plus
from typing import Optional

import yaml
import pandas as pd
from utils import disable_system_proxy

try:
    import taosws
except ImportError:
    print("请安装 taos-ws-py: pip install taos-ws-py", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ============================================================
# 配置加载
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

    env_password = os.getenv("TDENGINE_PASSWORD")
    if env_password:
        config['tdengine']['password'] = env_password

    return config


def setup_logging(config: dict):
    log_cfg = config.get('logger', {})
    level = getattr(logging, log_cfg.get('level', 'INFO').upper(), logging.INFO)
    log_file = PROJECT_ROOT / log_cfg.get('file', 'data/logs/fetcher.log')
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
# Rate Limiter (令牌桶)
# ============================================================

class RateLimiter:
    """滑动窗口速率限制器"""

    def __init__(self, rate_per_minute: int):
        self.rate = rate_per_minute
        self._window = []  # list[float] 请求时间戳

    def acquire(self):
        """阻塞直到可以发起下一次请求"""
        now = time.time()
        self._window = [t for t in self._window if now - t < 60]
        if len(self._window) >= self.rate:
            wait = 60 - (now - self._window[0]) + 0.2
            logging.debug(f"速率限制: 等待 {wait:.1f}s ...")
            time.sleep(wait)
            self._window = [t for t in self._window if time.time() - t < 60]
        self._window.append(time.time())

    @property
    def remaining(self) -> int:
        self._window = [t for t in self._window if time.time() - t < 60]
        return max(0, self.rate - len(self._window))


# ============================================================
# TDengine 连接器
# ============================================================

class TdengineConnector:
    """TDengine 3.x WebSocket 连接器"""

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
        try:
            dsn = (
                f"taosws://{quote_plus(self.username)}:"
                f"{quote_plus(self.password)}@"
                f"{self.host}:{self.port}"
            )
            self._conn = taosws.connect(dsn)
            self._conn.execute(f"USE {self.database}")
            logging.info(f"已连接 TDengine: {self.host}:{self.port}/{self.database}")
            return True
        except Exception as e:
            logging.error(f"连接 TDengine 失败: {e}")
            return False

    def ensure_subtable(self, stock_code: str, table_type: str):
        stable = "kline_daily" if table_type == "daily" else "kline_1m"
        table = f"d_{stock_code}" if table_type == "daily" else f"m_{stock_code}"
        market = "SZ" if stock_code.lower().startswith("sz") else "SH"

        try:
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "
                f"USING {stable} TAGS ('{stock_code}', '{market}')"
            )
        except Exception as e:
            logging.error(f"创建子表失败 {table}: {e}")
            raise

    def ensure_sentiment_subtable(self, table_name: str, model: str):
        try:
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table_name} "
                f"USING market_sentiment TAGS ('{model}')"
            )
        except Exception as e:
            logging.error(f"创建情绪子表失败 {table_name}: {e}")
            raise

    def get_latest_ts(self, table_name: str) -> Optional[datetime]:
        try:
            result = self._conn.query(f"SELECT ts FROM {table_name} ORDER BY ts DESC LIMIT 1")
            rows = list(result)
            if rows and len(rows) > 0 and rows[0]:
                ts_val = rows[0][0]
                if ts_val is None:
                    return None
                return pd.Timestamp(ts_val)
        except Exception as e:
            logging.debug(f"查询 {table_name} 最新 ts 失败（可能为空表）: {e}")
        return None

    def insert_kline(self, stock_code: str, table_type: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0

        table = f"d_{stock_code}" if table_type == "daily" else f"m_{stock_code}"
        self.ensure_subtable(stock_code, table_type)

        latest_ts = self.get_latest_ts(table)
        if latest_ts is not None:
            before = len(df)
            df['_ts_cmp'] = pd.to_datetime(df['ts']).dt.tz_localize(None)
            ref = pd.Timestamp(latest_ts).tz_localize(None)
            df = df[df['_ts_cmp'] > ref]
            df = df.drop(columns=['_ts_cmp'])
            skipped = before - len(df)
            if skipped > 0:
                logging.info(f"  {table}: 跳过 {skipped} 条已有数据")

        if df.empty:
            return 0

        total = 0
        batch_size = 500

        for start in range(0, len(df), batch_size):
            batch = df.iloc[start:start + batch_size]
            values = []
            for _, row in batch.iterrows():
                ts = row['ts']
                if isinstance(ts, (datetime, pd.Timestamp)):
                    ts_str = ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                else:
                    ts_str = str(ts)
                values.append(
                    f"('{ts_str}', {float(row['open'])}, {float(row['high'])}, "
                    f"{float(row['low'])}, {float(row['close'])}, "
                    f"{int(float(row['volume']))}, {float(row['amount'])})"
                )
            sql = f"INSERT INTO {table} VALUES " + " ".join(values)
            try:
                self._conn.execute(sql)
                total += len(batch)
            except Exception as e:
                logging.error(f"  {table} 批量写入失败 [{start}:{start+len(batch)}]: {e}")
                for idx, (_, row) in enumerate(batch.iterrows()):
                    try:
                        self._conn.execute(f"INSERT INTO {table} VALUES {values[idx]}")
                        total += 1
                    except Exception as e2:
                        logging.error(f"    单条写入失败 [{start+idx}]: {e2}")
        return total

    def insert_sentiment(self, table_name: str, model: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        self.ensure_sentiment_subtable(table_name, model)

        total = 0
        for _, row in df.iterrows():
            ts = row['ts']
            if isinstance(ts, (datetime, pd.Timestamp)):
                ts_str = ts.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            else:
                ts_str = str(ts)
            sql = (
                f"INSERT INTO {table_name} VALUES "
                f"('{ts_str}', {float(row.get('open', 0))}, {float(row.get('high', 0))}, "
                f"{float(row.get('low', 0))}, {float(row.get('close', 0))}, "
                f"{float(row.get('vol', 0))}, {float(row.get('amount', 0))})"
            )
            try:
                self._conn.execute(sql)
                total += 1
            except Exception as e:
                logging.error(f"  情绪写入失败 [{ts_str}]: {e}")
        return total

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logging.info("TDengine 连接已关闭")


# ============================================================
# zzshare 数据采集器
# ============================================================

class ZZShareFetcher:
    """自在量化 (zzshare) 数据源"""

    CODE_SUFFIX = {"sh": "SH", "sz": "SZ"}

    def __init__(self, config: dict):
        from zzshare.client import DataApi

        zz_cfg = config.get('zzshare', {})
        token = zz_cfg.get('token', '')
        self.api = DataApi(token=token) if token else DataApi()
        self.rate_limiter = RateLimiter(zz_cfg.get('rate_limit', 100))
        self.freq = zz_cfg.get('freq', '1min')
        self.history_days = config.get('fetch', {}).get('history_trading_days', 370)
        self.days_per_batch = config.get('fetch', {}).get('days_per_batch', 10)
        self.end_date = config.get('fetch', {}).get('end_date')
        self.collect_sentiment = zz_cfg.get('collect_sentiment', True)

    @staticmethod
    def to_zzshare_code(internal_code: str) -> str:
        prefix = internal_code[:2].lower()
        digits = internal_code[2:]
        suffix = ZZShareFetcher.CODE_SUFFIX.get(prefix, 'SZ')
        return f"{digits}.{suffix}"

    @staticmethod
    def to_internal_code(zzshare_code: str) -> str:
        digits, suffix = zzshare_code.split('.')
        prefix = 'sh' if suffix.upper() == 'SH' else 'sz'
        return f"{prefix}{digits}"

    def get_trading_days(self, count: int) -> list:
        self.rate_limiter.acquire()
        result = self.api.trade_days(days=count)
        if result is None:
            return []
        if isinstance(result, list):
            days = sorted(str(d) for d in result if d)
        elif hasattr(result, 'empty') and result.empty:
            return []
        else:
            for col in ('trade_date', 'cal_date', 'date'):
                if col in result.columns:
                    days = sorted(result[col].astype(str).tolist())
                    break
            else:
                days = sorted(result.iloc[:, 0].astype(str).tolist())

        if self.end_date:
            days = [d for d in days if str(d) <= self.end_date]
        return days

    def fetch_1m_day(self, ts_code: str, trade_date: str) -> pd.DataFrame:
        self.rate_limiter.acquire()
        try:
            result = self.api.stk_mins(ts_code=ts_code, trade_time=trade_date, freq=self.freq)
            if result is None:
                return pd.DataFrame()
            if isinstance(result, list):
                if not result:
                    return pd.DataFrame()
                df = pd.DataFrame(result)
            elif hasattr(result, 'empty') and result.empty:
                return pd.DataFrame()
            else:
                df = result.copy()
            df = df.rename(columns={'trade_time': 'ts', 'vol': 'volume'})
            ts_raw = df['ts'].astype(str).str.strip()
            df['ts'] = pd.to_datetime(ts_raw, format='%Y%m%d%H%M', errors='coerce')
            mask_na = df['ts'].isna()
            if mask_na.any():
                df.loc[mask_na, 'ts'] = pd.to_datetime(
                    ts_raw[mask_na], format='%Y%m%d%H%M%S', errors='coerce'
                )
            needed = ['ts', 'open', 'high', 'low', 'close', 'volume', 'amount']
            df = df[[c for c in needed if c in df.columns]]
            df['volume'] = df['volume'].fillna(0).astype(int)
            df = df.sort_values('ts', ascending=True).reset_index(drop=True)
            return df
        except Exception as e:
            logging.warning(f"  zzshare fetch 1m failed {ts_code} {trade_date}: {e}")
            return pd.DataFrame()

    def fetch_stock_history(self, internal_code: str, td: TdengineConnector) -> int:
        """拉取一只股票的分钟线历史，分批写入 TDengine"""
        zz_code = self.to_zzshare_code(internal_code)
        table = f"m_{internal_code}"

        trading_days = self.get_trading_days(self.history_days)
        if not trading_days:
            logging.warning(f"  {internal_code}: 无法获取交易日列表")
            return 0

        latest_ts = td.get_latest_ts(table)
        if latest_ts is not None:
            latest_date_str = latest_ts.strftime('%Y%m%d')
            trading_days = [d for d in trading_days if d > latest_date_str]

        if not trading_days:
            logging.info(f"  {internal_code}: 数据已是最新 (截止 {latest_ts})")
            return 0

        total_batches = (len(trading_days) + self.days_per_batch - 1) // self.days_per_batch
        total_written = 0
        batch_num = 0

        for start in range(0, len(trading_days), self.days_per_batch):
            batch_days = trading_days[start:start + self.days_per_batch]
            batch_num += 1
            all_data = []

            for day in batch_days:
                df = self.fetch_1m_day(zz_code, day)
                if not df.empty:
                    all_data.append(df)

            if all_data:
                merged = pd.concat(all_data, ignore_index=True)
                merged = merged.sort_values('ts').reset_index(drop=True)
                written = td.insert_kline(internal_code, '1m', merged)
                total_written += written

            progress = min(start + self.days_per_batch, len(trading_days))
            logging.info(
                f"  [{internal_code}] 批次 {batch_num}/{total_batches} "
                f"({progress}/{len(trading_days)}天) → 写入 {total_written} 条 "
                f"[速率: {self.rate_limiter.remaining}/{self.rate_limiter.rate}次可用]"
            )

        return total_written

    def fetch_market_sentiment(self, td: TdengineConnector) -> int:
        """采集市场情绪K线"""
        logging.info("采集市场情绪数据 ...")

        latest = td.get_latest_ts("sent_daily")
        if latest is not None:
            start_date = latest.strftime('%Y%m%d')
        else:
            start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')

        end_date = datetime.now().strftime('%Y%m%d')
        if start_date >= end_date:
            logging.info("  情绪数据已是最新")
            return 0

        self.rate_limiter.acquire()
        try:
            result = self.api.market_sentiment(date1=start_date, date2=end_date)
            if result is None:
                logging.warning("  市场情绪数据为空")
                return 0
            if isinstance(result, list):
                if not result:
                    logging.warning("  市场情绪数据为空")
                    return 0
                df = pd.DataFrame(result)
            elif isinstance(result, dict):
                inner = result.get('list') or result.get('data') or result.get('records')
                if inner and isinstance(inner, list) and len(inner) > 0:
                    df = pd.DataFrame(inner)
                else:
                    logging.warning(f"  市场情绪数据为空, keys: {list(result.keys())}")
                    return 0
            elif hasattr(result, 'empty') and result.empty:
                logging.warning("  市场情绪数据为空")
                return 0
            else:
                df = result.copy()
        except Exception as e:
            logging.error(f"  获取市场情绪失败: {e}")
            return 0

        col_map = {
            'trade_date': 'ts',
            'date': 'ts',
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        if 'ts' not in df.columns and 'trade_time' in df.columns:
            df = df.rename(columns={'trade_time': 'ts'})
        if 'ts' in df.columns:
            df['ts'] = pd.to_datetime(df['ts'], errors='coerce')

        written = td.insert_sentiment("sent_daily", "market_sentiment", df)
        logging.info(f"  市场情绪: 写入 {written} 条 ({start_date} ~ {end_date})")
        return written


# ============================================================
# AkShare 数据采集器 (备用)
# ============================================================

def _normalize_akshare_df(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {
        '时间': 'ts', '日期': 'ts',
        '开盘': 'open', '最高': 'high', '最低': 'low',
        '收盘': 'close', '成交量': 'volume', '成交额': 'amount',
    }
    df = df.rename(columns=col_map)
    needed = ['ts', 'open', 'high', 'low', 'close', 'volume', 'amount']
    df = df[[c for c in needed if c in df.columns]]
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.sort_values('ts').reset_index(drop=True)
    return df


def fetch_akshare_1m(stock_code: str) -> pd.DataFrame:
    import akshare as ak
    symbol = stock_code[2:]
    try:
        df = ak.stock_zh_a_hist_min_em(symbol=symbol, period="1", adjust="qfq")
    except Exception as e:
        logging.error(f"  AkShare 获取 {stock_code} 1m K线失败: {e}")
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    return _normalize_akshare_df(df)


def fetch_akshare_daily(stock_code: str, years: int = 2) -> pd.DataFrame:
    import akshare as ak
    symbol = stock_code[2:]
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=years * 366)).strftime('%Y%m%d')
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception as e:
        logging.error(f"  AkShare 获取 {stock_code} 日线失败: {e}")
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    return _normalize_akshare_df(df)


# ============================================================
# 股票列表
# ============================================================

def load_stock_list(csv_path: str) -> list[dict]:
    path = Path(csv_path)
    if not path.exists():
        logging.error(f"股票列表文件不存在: {path}")
        return []

    df = pd.read_csv(path, dtype=str)
    stocks = []
    for _, row in df.iterrows():
        s = {k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items()}
        stocks.append(s)
    logging.info(f"从 {csv_path} 加载了 {len(stocks)} 只股票")
    return stocks


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='A股历史行情数据采集')
    parser.add_argument('--config', default=None, help='配置文件路径')
    parser.add_argument('--stock', help='指定单只股票 (如 sh600036)')
    parser.add_argument('--daily', action='store_true', help='采集日线')
    parser.add_argument('--source', choices=['zzshare', 'akshare'], help='数据源 (默认读取 config)')
    parser.add_argument('--delay', type=float, help='股票间间隔/秒 (akshare模式)')
    parser.add_argument('--retry', type=int, default=3, help='失败重试次数')
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)
    disable_system_proxy()

    data_source = args.source or config.get('data', {}).get('data_source', 'zzshare')

    logging.info("=" * 60)
    logging.info(f"A股动态参数量化交易系统 - 数据采集 [{data_source}]")
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
                logging.error("股票列表为空，退出")
                return

        table_type = "daily" if args.daily else "1m"
        unit_label = "日线" if args.daily else "1分钟线"

        # ── zzshare 模式 ──
        if data_source == 'zzshare':
            if args.daily:
                logging.warning("zzshare 日线暂未实现，使用 AkShare fallback")
                data_source = 'akshare'
            else:
                zz = ZZShareFetcher(config)

                logging.info(f"配置: {len(stocks)}只股 × {zz.history_days}天 = "
                             f"约 {len(stocks) * zz.history_days} 次API调用 "
                             f"(限速 {zz.rate_limiter.rate}/分钟)")

                success = []
                failed = []
                start_time = time.time()

                for i, s in enumerate(stocks):
                    code = s.get('代码', s.get('code', ''))
                    name = s.get('名称', s.get('name', code))
                    logging.info(f"[{i + 1}/{len(stocks)}] {code} {name}")

                    try:
                        written = zz.fetch_stock_history(code, td)
                        if written > 0:
                            success.append(code)
                        else:
                            failed.append(code)
                    except Exception as e:
                        logging.error(f"  {code}: 采集异常: {e}")
                        failed.append(code)

                elapsed = time.time() - start_time
                logging.info("=" * 60)
                logging.info(f"采集完成: 成功 {len(success)}, 失败 {len(failed)}, 耗时 {elapsed/60:.1f}分钟")
                if failed:
                    logging.warning(f"失败: {', '.join(failed)}")
                logging.info("=" * 60)

                # 采集市场情绪
                if zz.collect_sentiment:
                    try:
                        zz.fetch_market_sentiment(td)
                    except Exception as e:
                        logging.error(f"市场情绪采集失败: {e}")

        # ── AkShare 模式 ──
        if data_source == 'akshare':
            fetch_1m = fetch_akshare_1m
            fetch_daily = fetch_akshare_daily
            retry_times = args.retry
            interval = args.delay or 5

            success = []
            failed = []

            for i, s in enumerate(stocks):
                code = s.get('代码', s.get('code', ''))
                name = s.get('名称', s.get('name', code))
                logging.info(f"[{i + 1}/{len(stocks)}] {code} {name}")

                df = pd.DataFrame()
                for attempt in range(retry_times):
                    if args.daily:
                        df = fetch_daily(code)
                    else:
                        df = fetch_1m(code)
                    if not df.empty:
                        break
                    if attempt < retry_times - 1:
                        wait = 5 * (attempt + 1)
                        logging.info(f"  {attempt + 2}/{retry_times} 次重试，等待 {wait}s ...")
                        time.sleep(wait)

                if df.empty:
                    logging.warning(f"  {code}: 获取{unit_label}数据失败")
                    failed.append(code)
                    continue

                logging.info(f"  API返回 {len(df)} 条 ({df['ts'].iloc[0]} ~ {df['ts'].iloc[-1]})")
                try:
                    written = td.insert_kline(code, table_type, df)
                    logging.info(f"  写入 {written} 条")
                    success.append(code) if written > 0 else failed.append(code)
                except Exception as e:
                    logging.error(f"  {code}: TDengine 写入失败: {e}")
                    failed.append(code)

                if i < len(stocks) - 1:
                    time.sleep(interval)

            logging.info("=" * 60)
            logging.info(f"采集完成: 成功 {len(success)}, 失败 {len(failed)}")
            if failed:
                logging.warning(f"失败: {', '.join(failed)}")
            logging.info("=" * 60)

    finally:
        td.close()


if __name__ == '__main__':
    main()
