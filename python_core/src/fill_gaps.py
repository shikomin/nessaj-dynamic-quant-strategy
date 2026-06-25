#!/usr/bin/env python3
"""
数据完整性工具 v2.2
====================

两种模式:
  1. 默认模式: 扫描所有股票的数据量, 报告完整性状态
  2. --retry-failed: 读取 data/logs/failed_days.csv, 定向补缺失败的交易日

===================================================================
模式 1: 健康检查
----------------
对每只股票 COUNT(*) 查询数据量, 与预期值比较, 报告三类结果:
  - 完整 (>95%): 达标
  - 不完整 (1%-95%): 差额超过 2 天, 需要补拉整只股票
  - 无数据 (0): 需要首次采集

===================================================================
模式 2: 失败补缺 (--retry-failed)
---------------------------------
读取 data_fetcher.py 生成的 data/logs/failed_days.csv,
按 (代码, 交易日) 逐条重新拉取并写入 TDengine。

用法:
  python fill_gaps.py                         # 扫描完整性
  python fill_gaps.py --retry-failed          # 补缺失败的交易日
"""
import sys
import time
import logging
import argparse
from pathlib import Path

import pandas as pd

# 将项目根目录加入 sys.path, 使 components/ 和 utils/ 模块可被导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.config import load_config, PROJECT_ROOT
from components.logger import setup_logging
from components.td_connector import TdConnector
from utils.proxy_utils import disable_system_proxy
from utils.data_utils import load_stock_list

# 与 data_fetcher.py 共享的常量
KLINE_COLS = ['ts', 'open', 'high', 'low', 'close', 'volume', 'amount']
CODE_SUFFIX = {"sh": "SH", "sz": "SZ", "bj": "BJ"}


def _to_zzshare(code: str) -> str:
    """内部代码 → zzshare 格式。"""
    prefix = code[:2].lower()
    digits = code[2:]
    suffix = CODE_SUFFIX.get(prefix, 'SZ')
    return f"{digits}.{suffix}"


def _normalize_date(d) -> str:
    """日期归一化为 YYYYMMDD。"""
    if hasattr(d, 'strftime'):
        return d.strftime('%Y%m%d')
    return str(d).replace('-', '').replace('/', '')


def _retry_failed_days(config: dict):
    """
    读取 failed_days.csv, 逐条重新拉取失败的交易日。

    复用 DataFetcher 的 _fetch_one_day 逻辑, 直接写入 TDengine。
    """
    from components.rate_limiter import RateLimiter
    from zzshare.client import DataApi

    zz_cfg = config.get('zzshare', {})
    fetch_cfg = config.get('fetch', {})

    api = DataApi(token=zz_cfg.get('token', ''))
    limiter = RateLimiter(zz_cfg.get('rate_limit', 60))
    freq = zz_cfg.get('freq', '1min')
    retry_times = fetch_cfg.get('retry_times', 3)
    retry_delay = fetch_cfg.get('retry_delay_base', 5)

    # ── 读取失败记录 ──
    fail_path = PROJECT_ROOT / "data" / "logs" / "failed_days.csv"
    if not fail_path.exists():
        logging.info(f"无失败记录文件: {fail_path}")
        return

    df_fail = pd.read_csv(fail_path, dtype=str)
    if df_fail.empty:
        logging.info("失败记录为空")
        return

    logging.info(f"读取失败记录: {len(df_fail)} 条")

    # ── 连接 TDengine ──
    td = TdConnector(config)
    if not td.connect():
        sys.exit(1)

    try:
        success, still_failed = 0, []

        for _, row in df_fail.iterrows():
            code = row['代码']
            day = row['交易日']
            zz_code = _to_zzshare(code)
            table = f"m_{code}"

            # 逐日重试
            limiter.acquire()
            for attempt in range(retry_times):
                try:
                    result = api.stk_mins(ts_code=zz_code, trade_time=day, freq=freq)
                    break
                except Exception as e:
                    if attempt < retry_times - 1:
                        time.sleep(retry_delay * (attempt + 1))
                    else:
                        logging.warning(f"  {code} {day}: {retry_times}次重试后仍失败: {e}")
                        result = None

            if result is None:
                still_failed.append(row.to_dict())
                continue

            # 标准化 + 写入
            df_kline = _normalize_kline_df(result)
            if df_kline.empty:
                still_failed.append(row.to_dict())
                logging.warning(f"  {code} {day}: 拉取为空")
                continue

            td.ensure_kline_subtable(code, '1m')
            written = td._insert_rows(table, df_kline, KLINE_COLS)
            if written > 0:
                success += 1
                logging.info(f"  {code} {day}: 补缺成功 ({written} 条)")
            else:
                still_failed.append(row.to_dict())
                logging.warning(f"  {code} {day}: INSERT 失败")

        logging.info(f"补缺结果: 成功 {success}, 仍失败 {len(still_failed)}")

        # 更新失败文件 (只保留仍失败的)
        if still_failed:
            pd.DataFrame(still_failed).to_csv(fail_path, index=False, encoding="utf-8-sig")
            logging.warning(f"仍有 {len(still_failed)} 条失败, 保留在 {fail_path}")
        else:
            fail_path.unlink(missing_ok=True)
            logging.info(f"全部补缺完成, 已删除 {fail_path}")

    finally:
        td.close()


def _normalize_kline_df(raw) -> pd.DataFrame:
    """
    标准化 zzshare 返回的 K线 DataFrame。
    与 data_fetcher.py 中 _normalize_kline_df 逻辑一致。
    """
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

    df = df.rename(columns={'trade_time': 'ts', 'vol': 'volume'})
    ts_raw = df['ts'].astype(str).str.strip()
    df['ts'] = pd.to_datetime(ts_raw, format='%Y%m%d%H%M', errors='coerce')
    mask_na = df['ts'].isna()
    if mask_na.any():
        df.loc[mask_na, 'ts'] = pd.to_datetime(
            ts_raw[mask_na], format='%Y%m%d%H%M%S', errors='coerce'
        )
    df = df[[c for c in KLINE_COLS if c in df.columns]]
    if 'volume' in df.columns:
        df['volume'] = df['volume'].fillna(0).astype(int)
    return df.sort_values('ts', ascending=True).reset_index(drop=True)


def _health_check(config: dict):
    """
    COUNT(*) 扫描所有股票的数据量, 报告完整性。
    """
    td = TdConnector(config)
    if not td.connect():
        sys.exit(1)

    try:
        stocks = load_stock_list(config['data'].get('stock_list', 'stock_list.csv'))
        if not stocks:
            logging.error("股票列表为空")
            return

        history_days = config.get('fetch', {}).get('history_trading_days', 370)
        expected_rows = int(history_days * 240 * 0.69)

        logging.info("=" * 60)
        logging.info(f"数据完整性检查: {len(stocks)} 只股票 (预期 ~{expected_rows} 条/股)")
        logging.info("=" * 60)

        complete, partial, empty = [], [], []

        for s in stocks:
            code = s.get('代码', '')
            name = s.get('名称', code)
            table = f"m_{code}"

            try:
                td.ensure_kline_subtable(code, '1m')
                rows = td.query(f"SELECT COUNT(*) FROM {table}")
                count = int(rows[0][0]) if rows and rows[0] and rows[0][0] else 0
            except Exception as e:
                logging.warning(f"  {code} {name:<8s}: 查询失败 ({e})")
                empty.append(code)
                continue

            if count == 0:
                logging.warning(f"  {code} {name:<8s}: 无数据")
                empty.append(code)
            elif count >= expected_rows * 0.95:
                logging.info(f"  {code} {name:<8s}: {count:>6}条, 完整")
                complete.append(code)
            else:
                pct = count / expected_rows * 100 if expected_rows > 0 else 0
                logging.warning(f"  {code} {name:<8s}: {count:>6}条 ({pct:.0f}%), "
                                f"不完整 → data_fetcher.py --stock {code}")
                partial.append(code)

        logging.info("=" * 60)
        logging.info(f"结果: 完整 {len(complete)}, 不完整 {len(partial)}, 无数据 {len(empty)}")
        if partial:
            logging.info(f"补缺命令: python data_fetcher.py --start-date YYYYMMDD --end-date YYYYMMDD "
                         f"--stock {' --stock '.join(partial)}")
        logging.info("=" * 60)

    finally:
        td.close()


def main():
    parser = argparse.ArgumentParser(description='数据完整性工具 v2.2')
    parser.add_argument('--config', default=None, help='配置文件路径')
    parser.add_argument('--retry-failed', action='store_true',
                        help='读取 failed_days.csv 定向补缺失败的交易日')
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config, "fill_gaps.log")
    disable_system_proxy()

    if args.retry_failed:
        logging.info("=" * 60)
        logging.info("失败交易日定向补缺")
        logging.info("=" * 60)
        _retry_failed_days(config)
    else:
        _health_check(config)


if __name__ == '__main__':
    main()
