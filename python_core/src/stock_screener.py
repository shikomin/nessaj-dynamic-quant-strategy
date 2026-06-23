#!/usr/bin/env python3
"""
A股短线标的筛选器
使用 AkShare 实时行情，按流动性+波动性筛选
"""
import sys
from pathlib import Path

import pandas as pd
import akshare as ak

from config import PROJECT_ROOT
from utils import disable_system_proxy


def fetch_all_stocks() -> pd.DataFrame:
    return ak.stock_zh_a_spot_em()


def filter_stocks(df: pd.DataFrame) -> pd.DataFrame:
    def is_valid(code, name):
        if str(code).startswith(('8', '4', '9')):
            return False
        if any(kw in str(name) for kw in ('ST', '退', 'N', 'C')):
            return False
        return True

    mask = df.apply(lambda r: is_valid(r.iloc[0], r.iloc[1]), axis=1)
    df = df[mask].copy()

    df = df[pd.to_numeric(df['换手率'], errors='coerce') > 3]
    df = df[pd.to_numeric(df['振幅'], errors='coerce') > 4]
    df = df[pd.to_numeric(df['成交量'], errors='coerce') > 10_000_000]

    return df


def score_stocks(df: pd.DataFrame) -> pd.DataFrame:
    turnover = pd.to_numeric(df['换手率'], errors='coerce')
    amplitude = pd.to_numeric(df['振幅'], errors='coerce')
    volume = pd.to_numeric(df['成交量'], errors='coerce')

    t_norm = (turnover - turnover.min()) / (turnover.max() - turnover.min() + 1e-9)
    a_norm = (amplitude - amplitude.min()) / (amplitude.max() - amplitude.min() + 1e-9)
    v_norm = (volume - volume.min()) / (volume.max() - volume.min() + 1e-9)

    df['score'] = t_norm * 0.35 + a_norm * 0.35 + v_norm * 0.30
    return df.sort_values('score', ascending=False)


def main():
    disable_system_proxy()
    print("正在获取全A股实时行情 ...")
    df_all = fetch_all_stocks()
    print(f"  全A股: {len(df_all)} 只")

    df_filtered = filter_stocks(df_all)
    print(f"  初筛后: {len(df_filtered)} 只 (换手>3% 振幅>4% 成交>1000万股)")

    if df_filtered.empty:
        print("没有股票通过初筛条件", file=sys.stderr)
        return

    df_scored = score_stocks(df_filtered)
    out = pd.DataFrame()
    out['代码'] = df_scored.iloc[:100, 0].apply(
        lambda c: f"sz{c}" if str(c).startswith(('0', '3')) else f"sh{c}"
    )
    out['名称'] = df_scored.iloc[:100, 1]
    out['market'] = out['代码'].apply(lambda c: 'SZ' if c.startswith('sz') else 'SH')

    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "stock_list_screened.csv"
    out.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n已写入: {output_path}")
    print(f"共 {len(out)} 只")


if __name__ == '__main__':
    main()
