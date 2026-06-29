#!/usr/bin/env python3
"""
截面归一化排名 v2.3
====================

在特征工程全部完成后运行，对每个分钟时间点，计算所有股票之间的特征排名分位。
写入 3 维截面特征: rank_ma5, rank_rsi_14, rank_volume。

===================================================================
算法
-----
逐交易日处理 (控制内存 ≤ 20MB/天):
  1. 加载该日所有股票的 32 维特征
  2. 对每分钟: 按 ma5 / rsi_14 / volume 计算 percentile rank (0~1)
  3. 每个股票保存排名数据到临时 parquet
全部日期处理完后:
  4. 对每个股票: 合并排名 → 加载 TDengine 特征 → merge → 重建 f_{code} 表

===================================================================
用法
----
  python cross_sectional.py                       # 全量
  python cross_sectional.py --stock sh600036      # 仅评估一只
  python cross_sectional.py --dry-run             # 仅估算
"""
import sys
import time
import logging
import argparse
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.config import load_config, PROJECT_ROOT
from components.logger import setup_logging
from components.td_connector import TdConnector, ALL_COLS_FEATURE_V2
from utils.proxy_utils import disable_system_proxy
from utils.data_utils import load_stock_list

# ============================================================
# 常量
# ============================================================

RANK_COLS = ['rank_ma5', 'rank_rsi_14', 'rank_volume']    # 输出的排名列
SRC_COLS  = ['ma5', 'rsi_14', 'volume']                     # 被排名的源特征列
TEMP_DIR  = "cross_sectional_tmp"                           # 临时文件目录


# ============================================================
# 核心: 逐日加载 + 排名计算
# ============================================================

def _get_unique_dates(td: TdConnector, stock_code: str) -> list[str]:
    """获取单只股票的所有交易日 (从 f_{code} 表中 DISTINCT ts 取日期)。"""
    try:
        rows = td.query(
            f"SELECT DISTINCT ts FROM f_{stock_code} ORDER BY ts ASC"
        )
        dates = set()
        for r in rows:
            if r[0]:
                dates.add(pd.Timestamp(r[0]).strftime('%Y%m%d'))
        return sorted(dates)
    except Exception:
        return []


def _load_day_features(td: TdConnector, code: str, date: str) -> pd.DataFrame:
    """
    加载单只股票单个交易日的特征数据。

    返回 DataFrame, 列 = ['ts'] + ALL_COLS_FEATURE_V2
    """
    date_start = f"{date[:4]}-{date[4:6]}-{date[6:8]} 00:00:00"
    date_end   = f"{date[:4]}-{date[4:6]}-{date[6:8]} 23:59:59"
    sql = (
        f"SELECT ts, {', '.join(ALL_COLS_FEATURE_V2)} "
        f"FROM f_{code} "
        f"WHERE ts >= '{date_start}' AND ts <= '{date_end}' "
        f"ORDER BY ts ASC"
    )
    try:
        rows = td.query(sql)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=['ts'] + ALL_COLS_FEATURE_V2)
        df['ts'] = pd.to_datetime(df['ts'])
        for c in ALL_COLS_FEATURE_V2:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        return df
    except Exception:
        return pd.DataFrame()


def _compute_ranks_for_day(
    td: TdConnector, stocks: list[dict], date: str, temp_dir: Path,
) -> int:
    """
    对单个交易日的所有股票计算截面排名，写入临时 parquet。

    返回: 有效股票数
    """
    # ── 加载所有股票该日的特征 ──
    daily_data = []
    for s in stocks:
        code = s.get('代码', '')
        df = _load_day_features(td, code, date)
        if df.empty:
            continue
        daily_data.append(df.assign(_code=code))

    if len(daily_data) < 10:
        return 0

    big_df = pd.concat(daily_data, ignore_index=True)

    # ── 每分钟计算 percentile rank (0~1) ──
    # rank(pct=True) 返回 0~1 的百分位排名, 值越大 = 在所有股票中越靠前
    for src, dst in zip(SRC_COLS, RANK_COLS):
        big_df[dst] = big_df.groupby('ts')[src].rank(pct=True)

    # ── 每个股票独立保存排名 ──
    saved = 0
    for code in big_df['_code'].unique():
        sub = big_df[big_df['_code'] == code][['ts'] + RANK_COLS].copy()
        if sub.empty:
            continue
        out_path = temp_dir / f"{date}_{code}.parquet"
        sub.to_parquet(out_path, index=False)
        saved += 1

    return saved


# ============================================================
# 合并排名 → 重建 f_{code} 表
# ============================================================

def _merge_and_rebuild(td: TdConnector, code: str, temp_dir: Path):
    """
    对单只股票: 合并所有临时排名文件 → 与 TDengine 特征 merge → 重建 f_{code}。

    步骤:
    1. 读取该股票的所有 {date}_{code}.parquet 文件
    2. merge 为一个大的排名 DataFrame
    3. 从 TDengine 加载 f_{code} 的完整特征数据
    4. merge_asof 对齐时间戳
    5. DROP f_{code} + CREATE + INSERT
    """
    # ── 1. 收集该股票的临时排名文件 ──
    rank_files = sorted(temp_dir.glob(f"*_{code}.parquet"))
    if not rank_files:
        return

    rank_parts = []
    for f in rank_files:
        try:
            df = pd.read_parquet(f)
            if not df.empty:
                rank_parts.append(df)
        except Exception:
            pass

    if not rank_parts:
        return

    ranks_df = pd.concat(rank_parts, ignore_index=True)
    ranks_df['ts'] = pd.to_datetime(ranks_df['ts'])
    ranks_df = ranks_df.sort_values('ts').reset_index(drop=True)

    # ── 2. 加载 TDengine 特征 ──
    table = f"f_{code}"
    rows = td.query(f"SELECT * FROM {table} ORDER BY ts ASC")
    if not rows:
        return

    col_order = ['ts'] + ALL_COLS_FEATURE_V2
    features_df = pd.DataFrame(rows, columns=col_order)
    features_df['ts'] = pd.to_datetime(features_df['ts'])
    for c in ALL_COLS_FEATURE_V2:
        features_df[c] = pd.to_numeric(features_df[c], errors='coerce')

    # ── 3. merge 排名进特征 ──
    features_df['_ts_temp'] = features_df['ts']
    ranks_df['_ts_temp'] = ranks_df['ts']

    merged = pd.merge_asof(
        features_df.sort_values('_ts_temp'),
        ranks_df[['_ts_temp'] + RANK_COLS].sort_values('_ts_temp'),
        on='_ts_temp',
        direction='nearest',
        tolerance=pd.Timedelta(seconds=30),
    )
    for c in RANK_COLS:
        if c in merged.columns:
            merged[c] = merged[c].fillna(0.5)  # 缺排名填中位数 0.5
        else:
            merged[c] = 0.5

    merged = merged.drop(columns=['_ts_temp'])

    # ── 4. 重建子表 ──
    td.execute(f"DROP TABLE IF EXISTS {table}")
    market = 'SZ' if code.lower().startswith('sz') else ('BJ' if code.lower().startswith('bj') else 'SH')
    td.execute(
        f"CREATE TABLE {table} "
        f"USING features_1m TAGS ('{code}', '{market}')"
    )

    # ── 5. 重新插入 ──
    write_cols = ['ts'] + ALL_COLS_FEATURE_V2
    written = td._insert_rows(table, merged[write_cols], write_cols)
    logging.info(f"  {code}: 重建完成 ({written} 条)")

    # ── 6. 清理临时文件 ──
    for f in rank_files:
        f.unlink(missing_ok=True)


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='截面归一化排名 v2.3')
    parser.add_argument('--config', default=None, help='配置文件路径')
    parser.add_argument('--stock', help='仅评估单只股票')
    parser.add_argument('--dry-run', action='store_true', help='仅估算')
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config, "cross_sectional.log")
    disable_system_proxy()

    td = TdConnector(config)
    if not td.connect():
        sys.exit(1)

    try:
        # ── 股票列表 ──
        if args.stock:
            stocks = [{'代码': args.stock, '名称': args.stock}]
        else:
            stocks = load_stock_list(config['data'].get('stock_list', 'stock_list.csv'))
            if not stocks:
                logging.error("股票列表为空")
                return

        # ── 获取日期范围 ──
        dates = _get_unique_dates(td, stocks[0].get('代码', ''))
        logging.info(f"截面归一化: {len(stocks)} 只股票, {len(dates)} 个交易日")

        if args.dry_run:
            logging.info(f"预估耗时: ~{len(dates) * 0.5:.0f}分钟 (排名计算) "
                         f"+ ~{len(stocks) * 3:.0f}分钟 (重建)")
            return

        # ══════════════════════════════════════════════════════
        # 阶段 1: 逐日计算排名 → 临时 parquet
        # ══════════════════════════════════════════════════════
        temp_dir = PROJECT_ROOT / "data" / TEMP_DIR
        temp_dir.mkdir(parents=True, exist_ok=True)

        # 清理旧临时文件
        for old in temp_dir.glob("*.parquet"):
            old.unlink(missing_ok=True)

        t0 = time.time()
        days_ok = 0
        for i, date in enumerate(dates):
            try:
                stock_count = _compute_ranks_for_day(td, stocks, date, temp_dir)
                if stock_count > 0:
                    days_ok += 1
                if (i + 1) % 50 == 0:
                    logging.info(f"  排名: [{i+1}/{len(dates)}] 天 ({stock_count}只/天)")
            except Exception as e:
                logging.error(f"  {date}: 排名计算失败: {e}")

        logging.info(f"排名计算完成: {days_ok}/{len(dates)} 天, "
                     f"耗时 {(time.time()-t0)/60:.1f}分钟")

        # ══════════════════════════════════════════════════════
        # 阶段 2: 逐只股票合并排名 → 重建 f_{code}
        # ══════════════════════════════════════════════════════
        t1 = time.time()
        for i, s in enumerate(stocks):
            code = s.get('代码', s.get('code', ''))
            try:
                _merge_and_rebuild(td, code, temp_dir)
            except Exception as e:
                logging.error(f"  {code}: 重建失败: {e}")
            if (i + 1) % 50 == 0:
                logging.info(f"  重建: [{i+1}/{len(stocks)}] 只")

        logging.info(f"重建完成: {len(stocks)} 只, "
                     f"耗时 {(time.time()-t1)/60:.1f}分钟")

        # 清理临时目录
        for leftover in temp_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        try:
            temp_dir.rmdir()
        except OSError:
            pass

        logging.info("=" * 60)
        logging.info("截面归一化全部完成")
        logging.info("=" * 60)

    finally:
        td.close()


if __name__ == '__main__':
    main()
