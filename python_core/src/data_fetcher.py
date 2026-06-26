#!/usr/bin/env python3
"""
数据采集模块 v2.2
==================

核心改进:
  1. 支持 --start-date / --end-date 指定采集日期范围
  2. 个股拉取完成后，自动检测并补全指数和情绪数据
  3. 纯配置驱动，无硬编码
  4. 断点续跑：已存在的交易日数据自动跳过

===================================================================
用法
----
  # 拉取 2025-06-01 到 2026-06-22 的全部数据
  python data_fetcher.py --start-date 20250601 --end-date 20260622

  # 只拉单只股票
  python data_fetcher.py --stock sh600036 --start-date 20250601 --end-date 20260622

  # 全量拉取 (从配置的 history_trading_days 计算起始日期)
  python data_fetcher.py

===================================================================
数据流
-------
1. zzshare.trade_days()  → 获取交易日历
2. 筛选 start_date ≤ 交易日 ≤ end_date
3. 遍历每只股票:
   a. 查询 TDengine 已有数据的最新日期
   b. 只拉取缺失的交易日的分钟线
   c. 按 days_per_batch 分批写入
4. 补全指数数据 (自动检测缺失)
5. 补全情绪数据 (自动检测缺失)

===================================================================
代码映射
---------
内部代码         zzshare 代码       TDengine 子表
sh600036         600036.SH          m_sh600036   (个股分钟线)
sh000001         000001.SH          i_sh000001   (指数分钟线)
sz399001         399001.SZ          i_sz399001
"""
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

# 将项目根目录加入 sys.path, 使 components/ 和 utils/ 模块可被导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.config import load_config, PROJECT_ROOT
from components.logger import setup_logging
from components.rate_limiter import RateLimiter
from components.td_connector import TdConnector
from utils.proxy_utils import disable_system_proxy
from utils.data_utils import load_stock_list

# ============================================================
# 常量
# ============================================================

# zzshare 代码后缀映射 (内部前缀 → zzshare 交易所后缀)
CODE_SUFFIX = {"sh": "SH", "sz": "SZ", "bj": "BJ"}

# K线标准列顺序 (与 TDengine kline_1m / index_kline_1m 超级表一致)
KLINE_COLS = ['ts', 'open', 'high', 'low', 'close', 'volume', 'amount']


# ============================================================
# 代码格式转换
# ============================================================

def internal_to_zzshare(internal_code: str) -> str:
    """
    内部代码 → zzshare 格式。

    示例:
      sh600036 → 600036.SH
      sz300774 → 300774.SZ
      sh000001 → 000001.SH (指数)
    """
    prefix = internal_code[:2].lower()
    digits = internal_code[2:]
    suffix = CODE_SUFFIX.get(prefix, 'SZ')
    return f"{digits}.{suffix}"


def _normalize_date(d) -> str:
    """
    将各种日期格式统一归一化为 YYYYMMDD 字符串。

    兼容: datetime.date, pd.Timestamp, str('2025-06-11'), str('20250611')
    zzshare trade_days() 可能返回 datetime.date 对象 (与版本有关),
    str() 后是 '2025-06-11' 带横杠, 与 YYYYMMDD 做字符串比较会出错。
    """
    if hasattr(d, 'strftime'):
        return d.strftime('%Y%m%d')
    return str(d).replace('-', '').replace('/', '')


# ============================================================
# 数据采集器
# ============================================================

class DataFetcher:
    """
    数据采集器 v2.2。

    封装 zzshare API 调用、速率控制、TDengine 写入、断点续跑逻辑。

    用法:
        fetcher = DataFetcher(config)
        fetcher.connect()
        trading_days = fetcher.get_trading_days('20250601', '20260622')
        fetcher.fetch_stock('sh600036', trading_days)
        fetcher.fetch_indices(trading_days)
        fetcher.fetch_sentiment('20250601', '20260622')
        fetcher.close()
    """

    def __init__(self, config: dict):
        """
        初始化 zzshare 客户端和速率限制器。

        配置来源: config.yaml 的 zzshare 和 fetch 节。
        """
        from zzshare.client import DataApi

        self._config = config
        zz_cfg = config.get('zzshare', {})
        fetch_cfg = config.get('fetch', {})

        token = zz_cfg.get('token', '')
        self._api = DataApi(token=token) if token else DataApi()

        self._rate_limiter = RateLimiter(zz_cfg.get('rate_limit', 60))
        self._freq = zz_cfg.get('freq', '1min')
        self._days_per_batch = fetch_cfg.get('days_per_batch', 10)
        self._retry_times = fetch_cfg.get('retry_times', 3)
        self._retry_delay = fetch_cfg.get('retry_delay_base', 5)
        self._auto_index = fetch_cfg.get('auto_index', True)
        self._auto_sentiment = fetch_cfg.get('auto_sentiment', True)

        # 指数列表来自配置
        self._indices = config.get('indices', [])

        # 单日拉取失败记录: {internal_code: [trade_date, ...]}
        self._failed_days: dict[str, list[str]] = {}

        self._td: Optional[TdConnector] = None

    # ── 连接管理 ──

    def connect(self) -> bool:
        """建立 TDengine 连接。"""
        self._td = TdConnector(self._config)
        return self._td.connect()

    def close(self):
        """关闭 TDengine 连接。"""
        if self._td:
            self._td.close()
            self._td = None

    # ── 交易日历 ──

    def get_trading_days(self, start_date: str, end_date: str) -> list[str]:
        """
        获取 start_date 到 end_date 之间的所有交易日。

        参数
        ----
        start_date: 起始日期 (YYYYMMDD)
        end_date:   结束日期 (YYYYMMDD)

        返回
        ----
        list[str]: 交易日列表，如 ['20250602', '20250603', ...]
        """
        logging.info(f"获取交易日历: {start_date} ~ {end_date}")

        # 先拉取足够的交易日 (max 750 天覆盖两年)
        self._rate_limiter.acquire()
        result = self._api.trade_days(days=750)

        if result is None:
            logging.error("无法获取交易日列表")
            return []

        # ── 兼容多种返回格式 + 统一归一化为 YYYYMMDD ──
        if isinstance(result, list):
            # result 元素可能是 datetime.date 或 str
            all_days = sorted(
                _normalize_date(d) for d in result if d
            )
        elif hasattr(result, 'empty') and result.empty:
            return []
        else:
            for col in ('trade_date', 'cal_date', 'date'):
                if col in result.columns:
                    all_days = sorted(
                        result[col].apply(_normalize_date).tolist()
                    )
                    break
            else:
                all_days = sorted(
                    result.iloc[:, 0].apply(_normalize_date).tolist()
                )

        # 过滤到指定日期范围 (双方都是 YYYYMMDD 纯数字, 字符串比较正确)
        days = [d for d in all_days if start_date <= d <= end_date]
        logging.info(f"  交易日: {len(days)} 天 (共 {len(all_days)} 天可用)")
        return days

    # ============================================================
    # 个股分钟线
    # ============================================================

    def fetch_stock(self, stock_code: str, trading_days: list[str]) -> int:
        """
        拉取单只股票在指定交易日的缺失分钟线。

        流程:
        1. 查询 TDengine m_{code} 表的最新日期
        2. 过滤掉已存在的交易日
        3. 按 days_per_batch 分批拉取 + 写入

        返回: 写入的总行数
        """
        zz_code = internal_to_zzshare(stock_code)
        table = f"m_{stock_code}"

        # ── 确保子表存在 ──
        self._td.ensure_kline_subtable(stock_code, '1m')

        # ── 确定哪些交易日需要拉取 ──
        latest_ts = self._td.get_latest_ts(table)
        if latest_ts is not None:
            latest_date = latest_ts.strftime('%Y%m%d')
            pending_days = [d for d in trading_days if d > latest_date]
            logging.info(f"  {stock_code}: 最新 {latest_date}, "
                         f"还需 {len(pending_days)}/{len(trading_days)} 天")
        else:
            pending_days = trading_days
            logging.info(f"  {stock_code}: 无历史数据, 拉取 {len(pending_days)} 天")

        if not pending_days:
            logging.info(f"  {stock_code}: 数据已是最新, 跳过")
            return 0

        # ── 分批拉取 ──
        return self._fetch_daily_bars(
            zz_code=zz_code,
            internal_code=stock_code,
            table=table,
            table_type='stock',
            trading_days=pending_days,
        )

    # ============================================================
    # 指数分钟线
    # ============================================================

    def fetch_indices(self, trading_days: list[str]) -> dict[str, int]:
        """
        拉取配置中所有指数的缺失分钟线。

        自动检测 TDengine 中是否已有数据，只拉取缺失部分。
        返回: {index_code: 写入行数}
        """
        if not self._indices:
            logging.info("未配置指数列表, 跳过")
            return {}

        logging.info(f"指数数据采集: {len(self._indices)} 个指数")
        results = {}

        for idx_cfg in self._indices:
            code = idx_cfg.get('code', '')
            name = idx_cfg.get('name', code)
            zz_code = internal_to_zzshare(code)
            table = f"i_{code}"

            # ── 确定缺失日期 ──
            latest_ts = self._td.get_latest_ts(table)
            if latest_ts is not None:
                latest_date = latest_ts.strftime('%Y%m%d')
                pending_days = [d for d in trading_days if d > latest_date]
                logging.info(f"  {code} ({name}): 最新 {latest_date}, "
                             f"还需 {len(pending_days)} 天")
            else:
                pending_days = trading_days
                logging.info(f"  {code} ({name}): 无历史数据, "
                             f"拉取 {len(pending_days)} 天")

            if not pending_days:
                logging.info(f"  {code} ({name}): 完整, 跳过")
                results[code] = 0
                continue

            # ── 确保子表存在 ──
            self._td.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "
                f"USING index_kline_1m TAGS ('{code}', '{name}')"
            )

            # ── 拉取 ──
            written = self._fetch_daily_bars(
                zz_code=zz_code,
                internal_code=code,
                table=table,
                table_type='index',
                trading_days=pending_days,
            )
            results[code] = written

        return results

    # ============================================================
    # 市场情绪
    # ============================================================

    def fetch_sentiment(self, start_date: str, end_date: str) -> int:
        """
        拉取市场情绪日K线数据。

        zzshare 的 market_sentiment API 返回一段时间内的情绪K线。
        存储于 TDengine market_sentiment 超级表 → sent_daily 子表。

        返回: 写入的行数
        """
        logging.info(f"市场情绪数据: {start_date} ~ {end_date}")

        # ── 检查已有数据 ──
        latest = self._td.get_latest_ts("sent_daily")
        if latest is not None:
            effective_start = latest.strftime('%Y-%m-%d')
            logging.info(f"  已有最新: {effective_start}")
        else:
            effective_start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
            logging.info(f"  无历史数据, 从 {effective_start} 开始")

        effective_end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        if effective_start >= effective_end:
            logging.info("  情绪数据已是最新, 跳过")
            return 0

        # ── 调用 API ──
        self._rate_limiter.acquire()
        try:
            result = self._api.market_sentiment(date1=effective_start, date2=effective_end)
        except Exception as e:
            logging.error(f"  获取市场情绪失败: {e}")
            return 0

        df = self._parse_sentiment_result(result)
        if df.empty:
            logging.warning("  情绪数据为空")
            return 0

        # ── 确保子表存在 ──
        self._td.execute(
            "CREATE TABLE IF NOT EXISTS sent_daily "
            "USING market_sentiment TAGS ('market_sentiment')"
        )

        # ── 写入 ──
        written = self._td._insert_rows("sent_daily", df, KLINE_COLS)
        logging.info(f"  情绪: 写入 {written} 条 ({effective_start} ~ {effective_end})")
        return written

    # ============================================================
    # 内部辅助
    # ============================================================

    def _fetch_daily_bars(
        self, zz_code: str, internal_code: str,
        table: str, table_type: str,
        trading_days: list[str],
    ) -> int:
        """
        按天分批拉取分钟K线 → 写入 TDengine。

        参数
        ----
        table_type: 'stock' (个股) 或 'index' (指数)

        返回: 写入的总行数。拉取失败的交易日自动记录到 self._failed_days。
        """
        total_batches = (len(trading_days) + self._days_per_batch - 1) // self._days_per_batch
        total_written = 0

        for batch_idx in range(total_batches):
            start = batch_idx * self._days_per_batch
            batch_days = trading_days[start:start + self._days_per_batch]

            # 逐日拉取，收集失败的天
            daily_frames = []
            failed_in_batch = []
            for day in batch_days:
                df = self._fetch_one_day(zz_code, day)
                if not df.empty:
                    daily_frames.append(df)
                else:
                    failed_in_batch.append(day)

            # 记录失败天数
            if failed_in_batch:
                self._failed_days.setdefault(internal_code, []).extend(failed_in_batch)

            if not daily_frames:
                logging.warning(
                    f"  [{internal_code}] 批次 {batch_idx + 1}/{total_batches} "
                    f"全部失败 ({len(failed_in_batch)} 天)"
                )
                continue

            # 合并本批 → 写入
            merged = pd.concat(daily_frames, ignore_index=True)
            merged = merged.sort_values('ts').reset_index(drop=True)

            written = self._td._insert_rows(table, merged, KLINE_COLS)
            total_written += written

            progress = min(start + self._days_per_batch, len(trading_days))
            status_parts = [f"写入 {written} 条"]
            if failed_in_batch:
                status_parts.append(f"失败 {len(failed_in_batch)} 天")
            logging.info(
                f"  [{internal_code}] 批次 {batch_idx + 1}/{total_batches} "
                f"({progress}/{len(trading_days)}天) → {', '.join(status_parts)} "
                f"[速率: {self._rate_limiter.remaining}/{self._rate_limiter.rate}]"
            )

        return total_written

    def _fetch_one_day(self, zz_code: str, trade_date: str) -> pd.DataFrame:
        """
        拉取单只股票/指数单日的 1分钟K线。

        返回: 标准化 DataFrame (列 = KLINE_COLS), 空 DataFrame 表示无数据。
        """
        self._rate_limiter.acquire()

        for attempt in range(self._retry_times):
            try:
                result = self._api.stk_mins(
                    ts_code=zz_code, trade_time=trade_date, freq=self._freq
                )
                break
            except Exception as e:
                if attempt < self._retry_times - 1:
                    wait = self._retry_delay * (attempt + 1)
                    logging.debug(f"    {zz_code} {trade_date} 重试 {attempt + 2}/{self._retry_times}, "
                                  f"等待 {wait}s: {e}")
                    time.sleep(wait)
                else:
                    logging.warning(f"  {zz_code} {trade_date}: {self._retry_times}次重试后仍失败: {e}")
                    return pd.DataFrame()
        else:
            return pd.DataFrame()

        return self._normalize_kline_df(result)

    def _normalize_kline_df(self, raw) -> pd.DataFrame:
        """
        标准化 zzshare 返回的 K线 DataFrame。

        处理步骤:
        1. 兼容 list/dict/DataFrame 多种返回格式
        2. 列名统一 (trade_time→ts, vol→volume)
        3. 时间解析 (YYYYMMDDHHMM / YYYYMMDDHHMMSS)
        4. 只保留 KLINE_COLS 列
        """
        # ── 格式兼容 ──
        if raw is None:
            return pd.DataFrame()
        if isinstance(raw, list):
            if not raw:
                return pd.DataFrame()
            df = pd.DataFrame(raw)
        elif hasattr(raw, 'empty') and raw.empty:
            return pd.DataFrame()
        else:
            df = raw.copy()

        # ── 列名标准化 ──
        df = df.rename(columns={'trade_time': 'ts', 'vol': 'volume'})

        # ── 时间解析 ──
        ts_raw = df['ts'].astype(str).str.strip()
        df['ts'] = pd.to_datetime(ts_raw, format='%Y%m%d%H%M', errors='coerce')
        mask_na = df['ts'].isna()
        if mask_na.any():
            df.loc[mask_na, 'ts'] = pd.to_datetime(
                ts_raw[mask_na], format='%Y%m%d%H%M%S', errors='coerce'
            )

        # ── 只保留需要的列 ──
        df = df[[c for c in KLINE_COLS if c in df.columns]]
        if 'volume' in df.columns:
            df['volume'] = df['volume'].fillna(0).astype(int)

        df = df.sort_values('ts', ascending=True).reset_index(drop=True)
        return df

    def _parse_sentiment_result(self, result) -> pd.DataFrame:
        """
        解析 zzshare 市场情绪 API 的返回结果。

        zzshare market_sentiment 返回格式:
          list[dict]: [{modal_id, date(YYYYMMDD), p_open, p_close, p_high, p_low, p_close_pre1d}]
        没有 volume/amount 字段, 统一填 0。
        """
        if result is None:
            return pd.DataFrame()
        if isinstance(result, list):
            if not result:
                return pd.DataFrame()
            df = pd.DataFrame(result)
        elif isinstance(result, dict):
            inner = result.get('list') or result.get('data') or result.get('records')
            if inner and isinstance(inner, list):
                df = pd.DataFrame(inner)
            else:
                return pd.DataFrame()
        else:
            df = result.copy()

        # ── 列名标准化: zzshare 情绪字段 → KLINE_COLS ──
        col_map = {
            'date': 'ts', 'trade_date': 'ts', 'trade_time': 'ts',
            'p_open': 'open', 'p_high': 'high', 'p_low': 'low', 'p_close': 'close',
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        # 补全缺失的列
        for missing in ['volume', 'amount']:
            if missing not in df.columns:
                df[missing] = 0

        if 'ts' in df.columns:
            df['ts'] = pd.to_datetime(df['ts'], errors='coerce')
        return df

    # ============================================================
    # 失败重试
    # ============================================================

    def retry_failed_days(self) -> dict[str, list[str]]:
        """
        重试所有已记录的失败交易日 (自动执行一次)。

        遍历 self._failed_days 中所有 (code, [days])，
        按天重新拉取并写入 TDengine。成功的日期从记录中移除，
        依然失败的保留用于后续 fill_gaps.py 补缺。

        返回: 仍然失败的记录 {code: [days]}
        """
        if not self._failed_days:
            logging.info("无失败记录, 跳过重试")
            return {}

        total_failed_days = sum(len(v) for v in self._failed_days.values())
        logging.info(f"─" * 40)
        logging.info(f"失败重试: {len(self._failed_days)} 只股票, {total_failed_days} 个交易日")

        retry_success = 0
        still_failed: dict[str, list[str]] = {}

        for code, days in self._failed_days.items():
            still_broken = []
            zz_code = internal_to_zzshare(code)
            table = f"m_{code}"

            for day in days:
                df = self._fetch_one_day(zz_code, day)
                if not df.empty:
                    written = self._td._insert_rows(table, df, KLINE_COLS)
                    if written > 0:
                        retry_success += 1
                        logging.info(f"  {code} {day}: 重试成功 ({written} 条)")
                    else:
                        still_broken.append(day)
                        logging.warning(f"  {code} {day}: INSERT 失败")
                else:
                    still_broken.append(day)
                    logging.warning(f"  {code} {day}: 重试仍然失败")

            if still_broken:
                still_failed[code] = still_broken

        logging.info(f"重试结果: 成功 {retry_success}, 仍失败 {sum(len(v) for v in still_failed.values())}")
        return still_failed


# ============================================================
# 主入口
# ============================================================

def _parse_date_arg(date_str: Optional[str], default: str) -> str:
    """解析日期参数，校验格式为 YYYYMMDD。"""
    if date_str is None:
        return default
    if len(date_str) != 8 or not date_str.isdigit():
        logging.error(f"日期格式错误: {date_str}, 应为 YYYYMMDD")
        sys.exit(1)
    return date_str


def main():
    parser = argparse.ArgumentParser(
        description='A股历史行情数据采集 v2.2 (个股 + 指数 + 情绪)'
    )
    parser.add_argument('--config', default=None, help='配置文件路径')
    parser.add_argument('--stock', help='指定单只股票 (如 sh600036)')
    parser.add_argument('--start-date', help='起始日期 YYYYMMDD (如 20250601)')
    parser.add_argument('--end-date', help='结束日期 YYYYMMDD (如 20260622)')
    parser.add_argument('--no-index', action='store_true', help='跳过指数数据采集')
    parser.add_argument('--no-sentiment', action='store_true', help='跳过情绪数据采集')
    args = parser.parse_args()

    # ── 初始化 ──
    config = load_config(args.config)
    setup_logging(config, "fetcher.log")
    disable_system_proxy()

    # ── 确定日期范围 ──
    today = datetime.now().strftime('%Y%m%d')
    history_days = config.get('fetch', {}).get('history_trading_days', 370)
    # 默认起始日期: 370 个自然日前，或从配置中的 end_date 往前推
    default_start = (datetime.now() - timedelta(days=history_days)).strftime('%Y%m%d')
    end_date = _parse_date_arg(args.end_date, today)

    # 优先用 CLI 参数，其次用配置中的 end_date，最后用今天
    config_end = config.get('fetch', {}).get('end_date', '')
    if not args.end_date and config_end and len(config_end) == 8:
        end_date = config_end

    start_date = _parse_date_arg(args.start_date, default_start)

    logging.info("=" * 60)
    logging.info(f"A股动态参数量化交易系统 - 数据采集 v2.2")
    logging.info(f"日期范围: {start_date} ~ {end_date}")
    logging.info("=" * 60)

    # ── 连接 TDengine ──
    fetcher = DataFetcher(config)
    if not fetcher.connect():
        sys.exit(1)

    try:
        # ══════════════════════════════════════════════════════
        # 步骤 1: 获取交易日历
        # ══════════════════════════════════════════════════════
        trading_days = fetcher.get_trading_days(start_date, end_date)
        if not trading_days:
            logging.error(f"日期范围内无交易日: {start_date} ~ {end_date}")
            return

        # ══════════════════════════════════════════════════════
        # 步骤 2: 拉取个股数据
        # ══════════════════════════════════════════════════════
        if args.stock:
            stocks = [{'代码': args.stock, '名称': args.stock}]
        else:
            stocks = load_stock_list(config['data'].get('stock_list', 'stock_list.csv'))

        if not stocks:
            logging.error("股票列表为空")
            return

        logging.info(f"个股采集: {len(stocks)} 只, {len(trading_days)} 个交易日")

        stock_success, stock_failed = 0, 0
        start_time = time.time()

        for i, s in enumerate(stocks):
            code = s.get('代码', s.get('code', ''))
            name = s.get('名称', s.get('name', code))
            logging.info(f"[{i + 1}/{len(stocks)}] {code} {name}")

            try:
                written = fetcher.fetch_stock(code, trading_days)
                if written > 0:
                    stock_success += 1
                else:
                    # 0 条写入可能是"已是最新"，不算失败
                    stock_success += 1
            except Exception as e:
                logging.error(f"  {code}: 采集异常: {e}")
                stock_failed += 1

        elapsed = time.time() - start_time
        logging.info(f"个股完成: 成功 {stock_success}, 失败 {stock_failed}, "
                     f"耗时 {elapsed / 60:.1f} 分钟")

        # ══════════════════════════════════════════════════════
        # 步骤 2.5: 自动重试失败的交易日
        # ══════════════════════════════════════════════════════
        still_failed = fetcher.retry_failed_days()

        # 写入仍失败的记录，供 fill_gaps.py 定向补缺
        if still_failed:
            fail_log_dir = PROJECT_ROOT / "data" / "logs"
            fail_log_dir.mkdir(parents=True, exist_ok=True)
            fail_path = fail_log_dir / "failed_days.csv"

            rows = []
            for code, days in still_failed.items():
                for day in days:
                    rows.append({"代码": code, "交易日": day})
            if rows:
                pd.DataFrame(rows).to_csv(fail_path, index=False, encoding="utf-8-sig")
                logging.warning(
                    f"仍有 {len(rows)} 个交易日拉取失败, "
                    f"已记录到 {fail_path}, 后续运行 fill_gaps.py 补缺"
                )

        # ══════════════════════════════════════════════════════
        # 步骤 3: 补全指数数据
        # ══════════════════════════════════════════════════════
        if not args.no_index and config.get('fetch', {}).get('auto_index', True):
            logging.info("─" * 40)
            try:
                idx_results = fetcher.fetch_indices(trading_days)
                logging.info(f"指数完成: {idx_results}")
            except Exception as e:
                logging.error(f"指数采集失败: {e}")
        else:
            logging.info("跳过指数采集")

        # ══════════════════════════════════════════════════════
        # 步骤 4: 补全情绪数据
        # ══════════════════════════════════════════════════════
        if not args.no_sentiment and config.get('fetch', {}).get('auto_sentiment', True):
            logging.info("─" * 40)
            try:
                fetcher.fetch_sentiment(start_date, end_date)
            except Exception as e:
                logging.error(f"情绪采集失败: {e}")
        else:
            logging.info("跳过情绪采集")

        logging.info("=" * 60)
        logging.info("全部采集完成")
        logging.info("=" * 60)

    finally:
        fetcher.close()


if __name__ == '__main__':
    main()
