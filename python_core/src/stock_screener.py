#!/usr/bin/env python3
"""
A股短线标的筛选器
使用 AkShare 实时行情数据，按流动性+波动性筛选适合短线的股票

筛选逻辑:
  1. 排除 ST / *ST / 退市 / 新股
  2. 换手率 > 3% (活跃)
  3. 振幅 > 4% (波动空间)
  4. 成交量 > 1000万股 (流动性)
  5. 按活跃度综合评分排序，取前100

输出: data/stock_list_100.csv
"""

import sys
from pathlib import Path

import akshare as ak
import pandas as pd

from utils import disable_system_proxy

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def fetch_all_stocks() -> pd.DataFrame:
    """获取全A股实时行情 (单次API调用，约5500只)"""
    df = ak.stock_zh_a_spot_em()
    return df


def filter_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """按短线标准筛选"""
    # 1. 排除 ST / 退市 / 新股(N开头) / 新三板
    def is_valid(code, name):
        bad_prefixes = ('8', '4', '9')  # 新三板
        if code.startswith(bad_prefixes):
            return False
        if any(kw in name for kw in ('ST', '退', 'N', 'C')):
            return False
        return True

    mask = df.apply(lambda r: is_valid(str(r['代码']), str(r['名称'])), axis=1)
    df = df[mask].copy()

    # 2. 换手率 > 3%
    turnover = pd.to_numeric(df['换手率'], errors='coerce')
    df = df[turnover > 3]

    # 3. 振幅 > 4%
    amplitude = pd.to_numeric(df['振幅'], errors='coerce')
    df = df[amplitude > 4]

    # 4. 成交量 > 1000万股
    volume = pd.to_numeric(df['成交量'], errors='coerce')
    df = df[volume > 10_000_000]

    return df


def score_stocks(df: pd.DataFrame) -> pd.DataFrame:
    """综合评分排序"""
    turnover = pd.to_numeric(df['换手率'], errors='coerce')
    amplitude = pd.to_numeric(df['振幅'], errors='coerce')
    volume = pd.to_numeric(df['成交量'], errors='coerce')

    # 归一化到 [0, 1]
    t_norm = (turnover - turnover.min()) / (turnover.max() - turnover.min() + 1e-9)
    a_norm = (amplitude - amplitude.min()) / (amplitude.max() - amplitude.min() + 1e-9)
    v_norm = (volume - volume.min()) / (volume.max() - volume.min() + 1e-9)

    df['score'] = t_norm * 0.35 + a_norm * 0.35 + v_norm * 0.30
    df = df.sort_values('score', ascending=False)
    return df


def to_csv_format(df: pd.DataFrame, top: int = 100) -> pd.DataFrame:
    """转换为 stock_list.csv 兼容格式"""
    df = df.head(top).copy()
    out = pd.DataFrame()
    out['代码'] = df['代码'].apply(
        lambda c: f"sz{c}" if str(c).startswith(('0', '3')) else f"sh{c}"
    )
    out['名称'] = df['名称']
    out['market'] = out['代码'].apply(lambda c: 'SZ' if c.startswith('sz') else 'SH')
    return out.reset_index(drop=True)


def main():
    disable_system_proxy()

    print("正在获取全A股实时行情 ...")
    df_all = fetch_all_stocks()
    print(f"  全A股: {len(df_all)} 只")

    df_filtered = filter_stocks(df_all)
    print(f"  初筛后: {len(df_filtered)} 只 (换手>3% 振幅>4% 成交>1000万股)")

    if df_filtered.empty:
        print("没有股票通过初筛条件，请放宽条件后重试", file=sys.stderr)
        sys.exit(1)

    df_scored = score_stocks(df_filtered)
    df_out = to_csv_format(df_scored, top=100)
    print(f"  最终选出: {len(df_out)} 只")

    # 输出到 data/
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_path = data_dir / "stock_list_100.csv"
    df_out.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"\n已写入: {output_path}")

    # 瞥一眼结果
    print("\n前10名:")
    for _, row in df_out.head(10).iterrows():
        code = row['代码']
        name = row['名称']
        orig = df_scored[df_scored['代码'].apply(lambda c: f"sz{c}" if str(c).startswith(('0','3')) else f"sh{c}") == code]
        if not orig.empty:
            r = orig.iloc[0]
            print(f"  {code} {name:　<6s}  换手率:{r['换手率']:.1f}%  振幅:{r['振幅']:.1f}%  量:{int(float(r['成交量'])/1e6)}M")


if __name__ == '__main__':
    main()
