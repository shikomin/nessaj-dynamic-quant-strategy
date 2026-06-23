#!/usr/bin/env python3
"""
数据完整性健康检查脚本
COUNT(*) 快速扫描所有股票 → 报告完整性状态
≥95% 完整 | <95% 不完整 | 无数据

缺失数据请用 data_fetcher.py 补:
  python data_fetcher.py --stock sh600036

用法:
  python fill_gaps.py
"""

import sys
import time
import logging
import argparse
from pathlib import Path
from urllib.parse import quote_plus

import yaml
import pandas as pd
from utils import disable_system_proxy

try:
    import taosws
except ImportError:
    print("请安装 taos-ws-py: pip install taos-ws-py", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = PROJECT_ROOT / "config" / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    log_cfg = config.get('logger', {})
    level = getattr(logging, log_cfg.get('level', 'INFO').upper(), logging.INFO)
    log_file = PROJECT_ROOT / "data" / "logs" / "health_check.log"
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


def main():
    parser = argparse.ArgumentParser(description='数据完整性健康检查')
    parser.add_argument('--config', default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)
    disable_system_proxy()

    # Connect
    cfg = config['tdengine']
    try:
        conn = taosws.connect(
            f"taosws://{quote_plus(cfg.get('username','root'))}:"
            f"{quote_plus(cfg.get('password','taosdata'))}@"
            f"{cfg['host']}:{cfg['port']}"
        )
        conn.execute(f"USE {cfg['database']}")
    except Exception as e:
        logging.error(f"TDengine 连接失败: {e}")
        sys.exit(1)

    # Load stocks
    csv_path = PROJECT_ROOT / config['data'].get('stock_list', 'stock_list.csv')
    df_stocks = pd.read_csv(csv_path, dtype=str)
    stocks = [{k.strip(): v.strip() for k, v in row.items()}
              for _, row in df_stocks.iterrows()]

    history_days = config.get('fetch', {}).get('history_trading_days', 370)
    expected_rows = history_days * 240 * 0.69  # ~252 trading days × 240

    logging.info("=" * 60)
    logging.info(f"数据完整性检查: {len(stocks)} 只股票 (预期 ~{int(expected_rows)} 条/股)")
    logging.info("=" * 60)

    complete = []
    partial = []
    empty = []

    for s in stocks:
        code = s.get('代码', '')
        name = s.get('名称', code)
        table = f"m_{code}"

        try:
            # Ensure subtable exists
            market = "SZ" if code.lower().startswith("sz") else "SH"
            try:
                conn.execute(f"CREATE TABLE IF NOT EXISTS {table} USING kline_1m TAGS ('{code}', '{market}')")
            except:
                pass

            result = conn.query(f"SELECT COUNT(*) FROM {table}")
            rows = list(result)
            count = int(rows[0][0]) if rows and rows[0] and rows[0][0] else 0
        except Exception as e:
            logging.warning(f"  {code} {name}: 查询失败 ({e})")
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
            logging.warning(f"  {code} {name:<8s}: {count:>6}条 ({pct:.0f}%), 不完整 → 用 data_fetcher.py --stock {code} 补")
            partial.append(code)

    conn.close()

    logging.info("=" * 60)
    logging.info(f"结果: 完整 {len(complete)}, 不完整 {len(partial)}, 无数据 {len(empty)}")
    if partial:
        cmd = " \\\n  ".join(f"--stock {c}" for c in partial)
        logging.info(f"补缺命令: python data_fetcher.py {cmd}")
    logging.info("=" * 60)


if __name__ == '__main__':
    main()
