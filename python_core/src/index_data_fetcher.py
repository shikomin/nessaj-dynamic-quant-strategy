#!/usr/bin/env python3
"""
指数分钟线数据采集 — 基于 pytdx (通达信行情协议)
=================================================

用法:
  python index_data_fetcher.py
  python index_data_fetcher.py --start-date 20250601 --end-date 20260626

数据流:
  1. 从 config.yaml 读取指数列表和 pytdx 服务器地址
  2. 通过 pytdx.get_index_bars() 分页拉取指数 1分钟K线
  3. 写入 TDengine index_kline_1m 超级表 → i_{code} 子表
  4. 断点续跑: 自动跳过已有数据, 多服务器 fallback

pytdx 指数映射:
  sh000001 → market=1, code='000001'  (上证指数)
  sh000688 → market=1, code='000688'  (科创50)
  sz399001 → market=0, code='399001'  (深证成指)
  sz399006 → market=0, code='399006'  (创业板指)

pytdx 分页机制:
  get_index_bars(start=0, count=800) → 最近 800 条
  get_index_bars(start=800, count=800) → 前 800 条
  ...递增 start 直到返回空或数据早于目标起始日期
"""
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.config import load_config, PROJECT_ROOT
from components.logger import setup_logging
from components.td_connector import TdConnector

# ============================================================
# 常量
# ============================================================

KLINE_COLS = ['ts', 'open', 'high', 'low', 'close', 'volume', 'amount']

# 指数内部代码 → (pytdx market, pytdx code)
# market: 1=上海, 0=深圳
INDEX_MAP = {
    'sh000001': (1, '000001'),
    'sh000688': (1, '000688'),
    'sz399001': (0, '399001'),
    'sz399006': (0, '399006'),
}

# pytdx category 参数
CATEGORY_1MIN = 9  # 1分钟线

# 单次请求最大返回行数 (pytdx 服务端限制)
BARS_PER_REQUEST = 800


# ============================================================
# 指数数据采集器
# ============================================================

class IndexDataFetcher:
    """
    基于 pytdx 的指数分钟线采集器。

    用法:
        fetcher = IndexDataFetcher(config)
        fetcher.fetch_all('20250601', '20260626')
        fetcher.close()
    """

    def __init__(self, config: dict):
        self._config = config
        self._td: Optional[TdConnector] = None

        ptdx_cfg = config.get('pytdx', {})
        self._servers = ptdx_cfg.get('servers', [])
        self._interval = ptdx_cfg.get('interval', 1.0)
        self._indices = config.get('indices', [])

        if not self._servers:
            raise ValueError("pytdx 服务器未配置 (config.yaml → pytdx → servers)")

        # 服务端连接和游标 (懒连接)
        self._api = None
        self._current_server = None
        self._last_call = 0.0

        from pytdx.hq import TdxHq_API
        self._TdxHq_API = TdxHq_API

    def connect(self) -> bool:
        self._td = TdConnector(self._config)
        return self._td.connect()

    def close(self):
        if self._td:
            self._td.close()
            self._td = None
        if self._api:
            try:
                self._api.disconnect()
            except Exception:
                pass
            self._api = None

    # ── pytdx 连接管理 ──

    def _get_api(self):
        """
        获取或创建 pytdx 连接, 支持多服务器 fallback。
        """
        if self._api:
            return self._api

        api = self._TdxHq_API()
        for i, srv in enumerate(self._servers):
            host, port = srv['host'], srv['port']
            try:
                if api.connect(host, port):
                    self._api = api
                    self._current_server = (host, port)
                    logging.info(f"  pytdx 已连接: {host}:{port}")
                    return api
            except Exception as e:
                logging.warning(f"  pytdx {host}:{port} 连接失败: {e}")

        raise ConnectionError(f"所有 pytdx 服务器连接失败, 共 {len(self._servers)} 个")

    # ── 主流程 ──

    def fetch_all(self, start_date: str, end_date: str) -> dict[str, int]:
        """
        拉取配置中所有指数的分钟线。

        返回: {index_code: 写入行数}
        """
        if not self._indices:
            logging.warning("未配置指数列表")
            return {}

        logging.info(f"指数分钟线采集: {len(self._indices)} 个指数, {start_date} ~ {end_date}")
        results = {}

        for idx_cfg in self._indices:
            code = idx_cfg.get('code', '')
            name = idx_cfg.get('name', code)

            if code not in INDEX_MAP:
                logging.warning(f"  {code}: 未知指数, 跳过")
                continue

            market, tdx_code = INDEX_MAP[code]
            table = f"i_{code}"
            market_name = "上海" if market == 1 else "深圳"

            logging.info(f"  {code} ({name}) pytdx: market={market}, code={tdx_code} ({market_name})")

            # ── 确定已有数据 ──
            latest_ts = self._td.get_latest_ts(table)
            if latest_ts is not None:
                effective_start = latest_ts.strftime('%Y%m%d')
                logging.info(f"    已有最新: {latest_ts}")
                # 如果已有数据比 start_date 新, 从已有之后开始
                if effective_start >= start_date:
                    fetch_start = effective_start
                else:
                    fetch_start = start_date
            else:
                fetch_start = start_date

            if fetch_start >= end_date:
                logging.info(f"    已完整, 跳过")
                results[code] = 0
                continue

            # ── 确保子表存在 ──
            self._td.execute(
                f"CREATE TABLE IF NOT EXISTS {table} "
                f"USING index_kline_1m TAGS ('{code}', '{name}')"
            )

            # ── 分页拉取 ──
            written = self._fetch_index(market, tdx_code, table, fetch_start, end_date)
            results[code] = written
            logging.info(f"    写入 {written} 条")

        return results

    def _fetch_index(self, market: int, tdx_code: str, table: str,
                     target_start: str, target_end: str) -> int:
        """
        分页拉取单只指数所有 1分钟K线, 写入 TDengine。

        pytdx 分页: start=0 是最新数据, start+=800 向前翻页。
        持续翻页直到返回空或数据早于 target_start。
        """
        total_written = 0
        offset = 0
        cumulative = []  # 攒到一定量再批量写入

        while True:
            self._rate_wait()
            api = self._get_api()

            try:
                raw = api.get_index_bars(CATEGORY_1MIN, market, tdx_code, offset, BARS_PER_REQUEST)
            except Exception as e:
                logging.error(f"    get_index_bars error (offset={offset}): {e}")
                # 尝试断开重连下一个服务器
                try:
                    api.disconnect()
                except Exception:
                    pass
                self._api = None
                continue

            if raw is None or len(raw) == 0:
                break

            df = self._parse_bars(raw)
            if df.empty:
                break

            # ── 过滤: 只保留 target_start ≤ 日期 ≤ target_end ──
            df = df[(df['_date'] >= target_start) & (df['_date'] <= target_end)]
            if df.empty:
                # 所有数据都早于 target_start → 停止翻页
                if self._parse_date(raw[-1][0]) < target_start:
                    break
                # 也可能全部晚于 target_end (最新数据还没到), 继续翻页
                offset += BARS_PER_REQUEST
                continue

            df = df.drop(columns=['_date'])
            cumulative.append(df)

            # ── 判断是否继续翻页 ──
            oldest_date = self._parse_date(raw[-1][0])
            if oldest_date <= target_start:
                break

            offset += BARS_PER_REQUEST
            logging.debug(f"    offset={offset}, 已收集 {sum(len(d) for d in cumulative)} 条")

        if not cumulative:
            return 0

        # ── 合并去重写入 ──
        merged = pd.concat(cumulative, ignore_index=True)
        merged = merged.drop_duplicates(subset=['ts']).sort_values('ts', ascending=True)
        merged = merged.reset_index(drop=True)

        total_written = self._td._insert_rows(table, merged, KLINE_COLS)
        return total_written

    def _parse_bars(self, raw: list) -> pd.DataFrame:
        """
        解析 pytdx get_index_bars 返回数据。

        返回格式 (每行 tuple):
          (datetime, open, high, low, close, volume, amount, ...)
          datetime 是字符串如 '202506010930'
        转换为统一 DataFrame。
        """
        rows = []
        for r in raw:
            try:
                dt_str = str(r[0]).strip()
                # pytdx 返回 '202506010930' 格式或 '2025-06-01 09:30'
                if len(dt_str) == 12 and dt_str.isdigit():
                    dt = datetime.strptime(dt_str, '%Y%m%d%H%M')
                elif ' ' in dt_str:
                    dt = pd.to_datetime(dt_str)
                else:
                    dt = pd.to_datetime(dt_str)
                date_str = dt.strftime('%Y%m%d')
                ts_str = dt.strftime('%Y-%m-%d %H:%M:%S')

                rows.append({
                    'ts': ts_str,
                    'open': float(r[1]) if r[1] is not None else 0.0,
                    'high': float(r[2]) if r[2] is not None else 0.0,
                    'low': float(r[3]) if r[3] is not None else 0.0,
                    'close': float(r[4]) if r[4] is not None else 0.0,
                    'volume': int(float(r[5])) if r[5] is not None else 0,
                    'amount': float(r[6]) if len(r) > 6 and r[6] is not None else 0.0,
                    '_date': date_str,
                })
            except Exception as e:
                logging.debug(f"    parse bar error: {e}, raw={r[:4]}")
                continue

        return pd.DataFrame(rows) if rows else pd.DataFrame()

    @staticmethod
    def _parse_date(dt_raw) -> str:
        """从 pytdx 时间字符串提取 YYYYMMDD"""
        s = str(dt_raw).strip()
        if len(s) >= 8:
            if s.isdigit():
                return s[:8]
            try:
                return pd.to_datetime(s).strftime('%Y%m%d')
            except Exception:
                pass
        return s[:8]

    # ── 速率控制 ──

    def _rate_wait(self):
        """确保两次请求间隔不小于 interval 秒, 防止被封 IP"""
        elapsed = time.time() - self._last_call
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last_call = time.time()


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='指数分钟线数据采集 (基于 pytdx)')
    parser.add_argument('--config', default=None, help='配置文件路径')
    parser.add_argument('--start-date', help='起始日期 YYYYMMDD')
    parser.add_argument('--end-date', help='结束日期 YYYYMMDD')
    parser.add_argument('--index', help='指定单个指数 (如 sh000001)')
    args = parser.parse_args()

    # ── 初始化 ──
    config = load_config(args.config)
    setup_logging(config, "index_fetcher.log")

    # ── 日期范围 ──
    today = datetime.now().strftime('%Y%m%d')
    history_days = config.get('fetch', {}).get('history_trading_days', 250)
    default_start = (datetime.now() - timedelta(days=history_days)).strftime('%Y%m%d')

    end_date = args.end_date or today
    start_date = args.start_date or default_start

    if start_date >= end_date:
        logging.error(f"起始日期 >= 结束日期: {start_date} >= {end_date}")
        sys.exit(1)

    # ── 单指数模式 ──
    if args.index:
        config['indices'] = [{'code': args.index, 'name': args.index}]

    logging.info("=" * 60)
    logging.info("指数分钟线数据采集 (pytdx)")
    indices_codes = [i['code'] for i in config.get('indices', [])]
    logging.info(f"指数: {indices_codes}")
    logging.info(f"日期范围: {start_date} ~ {end_date}")
    logging.info("=" * 60)

    # ── 连接 ──
    fetcher = IndexDataFetcher(config)
    if not fetcher.connect():
        logging.error("TDengine 连接失败")
        sys.exit(1)

    try:
        results = fetcher.fetch_all(start_date, end_date)
        logging.info("=" * 60)
        logging.info(f"采集完成: {results}")
        logging.info("=" * 60)
    finally:
        fetcher.close()


if __name__ == '__main__':
    main()
