#!/usr/bin/env python3
"""
合并分样本文件 → 时序排序 → 切分 train.parquet / val.parquet

用法:
  python merge_samples.py                     # 合并 data/samples/ 下所有 .parquet
  python merge_samples.py --train-ratio 0.8   # 自定义比例
"""
import sys
import logging
import argparse
from pathlib import Path

import pandas as pd

from config import PROJECT_ROOT
from logger import setup_logging


def main():
    parser = argparse.ArgumentParser(description='合并样本文件')
    parser.add_argument('--config', default=None)
    parser.add_argument('--train-ratio', type=float, default=0.8)
    parser.add_argument('--samples-dir', default=None, help='分样本目录，默认 data/samples/')
    args = parser.parse_args()

    from config import load_config
    config = load_config(args.config) if args.config else {}
    setup_logging(config, "merge_samples.log")

    samples_dir = Path(args.samples_dir) if args.samples_dir else PROJECT_ROOT / "data" / "samples"
    if not samples_dir.exists():
        logging.error(f"样本目录不存在: {samples_dir}")
        sys.exit(1)

    files = list(samples_dir.glob("*.parquet"))
    if not files:
        logging.error(f"无 .parquet 文件: {samples_dir}")
        sys.exit(1)

    logging.info(f"找到 {len(files)} 个分样本文件")

    # 读取并合并
    dfs = []
    total_files = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            if not df.empty and 'feature_start_ts' in df.columns:
                dfs.append(df)
                total_files += 1
            else:
                logging.warning(f"  跳过空文件: {f.name}")
        except Exception as e:
            logging.warning(f"  读取失败 {f.name}: {e}")

    if not dfs:
        logging.error("无有效样本")
        sys.exit(1)

    df_all = pd.concat(dfs, ignore_index=True)
    logging.info(f"合并 {total_files} 个文件 → {len(df_all)} 条样本")

    # 按时间排序（时序铁律）
    df_all['_sort_ts'] = pd.to_datetime(df_all['feature_start_ts'])
    df_all = df_all.sort_values('_sort_ts').drop(columns=['_sort_ts']).reset_index(drop=True)

    # 切分
    n_train = int(len(df_all) * args.train_ratio)
    df_train = df_all[:n_train]
    df_val   = df_all[n_train:]

    train_path = PROJECT_ROOT / "data" / "train.parquet"
    val_path   = PROJECT_ROOT / "data" / "val.parquet"

    df_train.to_parquet(train_path, index=False, engine='pyarrow')
    df_val.to_parquet(val_path, index=False, engine='pyarrow')

    size_mb = (train_path.stat().st_size + val_path.stat().st_size) / 2**20
    logging.info(f"train: {len(df_train)}条 ({train_path})")
    logging.info(f"val:   {len(df_val)}条 ({val_path})")
    logging.info(f"总大小: {size_mb:.1f}MB")


if __name__ == '__main__':
    main()
