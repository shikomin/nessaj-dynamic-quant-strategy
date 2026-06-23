#!/usr/bin/env python3
"""
数据完整性健康检查
COUNT(*) 扫描 → 报告完整性状态。缺失用 data_fetcher.py --stock 补。
"""
import sys
import logging
import argparse

import pandas as pd

from config import load_config, PROJECT_ROOT
from logger import setup_logging
from td_connector import TdConnector
from utils import disable_system_proxy


def main():
    parser = argparse.ArgumentParser(description='数据完整性健康检查')
    parser.add_argument('--config', default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config, "health_check.log")
    disable_system_proxy()

    td = TdConnector(config)
    if not td.connect():
        sys.exit(1)

    try:
        csv_path = PROJECT_ROOT / config['data'].get('stock_list', 'stock_list.csv')
        df_stocks = pd.read_csv(csv_path, dtype=str)
        stocks = [{k.strip(): v.strip() for k, v in row.items()} for _, row in df_stocks.iterrows()]

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
                logging.warning(f"  {code} {name:<8s}: {count:>6}条 ({pct:.0f}%), 不完整 → data_fetcher.py --stock {code}")
                partial.append(code)

        logging.info("=" * 60)
        logging.info(f"结果: 完整 {len(complete)}, 不完整 {len(partial)}, 无数据 {len(empty)}")
        if partial:
            logging.info(f"补缺: python data_fetcher.py {' --stock '.join(partial)}")
        logging.info("=" * 60)

    finally:
        td.close()


if __name__ == '__main__':
    main()
