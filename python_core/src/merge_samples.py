#!/usr/bin/env python3
"""
合并分样本文件 → 时序排序 → 切分 train.parquet / val.parquet

===================================================================
为什么需要这个脚本？
---------------------
sample_generator.py 为每只股票生成独立的 data/samples/{code}.parquet 文件。
这个脚本把所有分文件合并为一个大数据集, 按时间排序后切分为训练集和验证集。

===================================================================
关键设计
---------
1. 按时间切分 (时序铁律): 用前 80% 时间的数据训练, 后 20% 时间验证。
   **严禁随机打乱** —— 时序数据 shuffling 会造成未来函数, 导致过拟合评测失真。

2. 时间排序依据: feature_start_ts 列 (特征窗口起始时间)。
   每个窗口的 4 条样本 (Plan B 正负样本) 共享相同的特征窗口时间,
   在排序中会相邻, 这是预期行为。

3. 验证集用于早停 (Early Stopping): 训练过程中监控验证 Loss,
   20 epoch 不降即停止, 防止过拟合。

===================================================================
输出文件
---------
data/train.parquet: 前 80% 的样本 (按时间排序)
data/val.parquet:   后 20% 的样本 (按时间排序)

列结构 (与 sample_generator.py 生成的列一致):
  features_blob, strategy_label, p1-p5, calmar, sample_weight,
  feature_start_ts, stock_code

用法:
  python merge_samples.py                        # 默认 80/20 切分
  python merge_samples.py --train-ratio 0.7       # 自定义比例
  python merge_samples.py --samples-dir data/custom/  # 自定义目录
"""
import sys
import logging
import argparse
from pathlib import Path

import pandas as pd

# 将项目根目录加入 sys.path, 使 components/ 和 utils/ 模块可被导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.config import PROJECT_ROOT
from components.logger import setup_logging


def main():
    parser = argparse.ArgumentParser(description='合并样本文件')
    parser.add_argument('--config', default=None)
    parser.add_argument('--train-ratio', type=float, default=0.8,
                        help='训练集比例 (默认 0.8)')
    parser.add_argument('--samples-dir', default=None,
                        help='分样本目录, 默认 data/samples/')
    args = parser.parse_args()

    from components.config import load_config
    config = load_config(args.config) if args.config else {}
    setup_logging(config, "merge_samples.log")

    # ── 定位分样本目录 ──
    samples_dir = (Path(args.samples_dir) if args.samples_dir
                   else PROJECT_ROOT / "data" / "samples")
    if not samples_dir.exists():
        logging.error(f"样本目录不存在: {samples_dir}")
        sys.exit(1)

    # ── 收集所有 .parquet 分文件 ──
    files = list(samples_dir.glob("*.parquet"))
    if not files:
        logging.error(f"无 .parquet 文件: {samples_dir}")
        sys.exit(1)

    logging.info(f"找到 {len(files)} 个分样本文件")

    # ── 读取并合并 ──
    dfs = []
    total_files = 0
    for f in files:
        try:
            df = pd.read_parquet(f)
            # 只保留有 feature_start_ts 列的文件 (有效样本)
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

    # ── 按时间排序 (时序铁律: 用过去预测未来) ──
    df_all['_sort_ts'] = pd.to_datetime(df_all['feature_start_ts'])
    df_all = df_all.sort_values('_sort_ts').drop(columns=['_sort_ts']).reset_index(drop=True)

    # ── 时序切分 ──
    # 前 n_train 条 = 训练集, 剩余 = 验证集
    n_train = int(len(df_all) * args.train_ratio)
    df_train = df_all[:n_train]
    df_val   = df_all[n_train:]

    # ── 写入最终文件 ──
    train_path = PROJECT_ROOT / "data" / "train.parquet"
    val_path   = PROJECT_ROOT / "data" / "val.parquet"

    df_train.to_parquet(train_path, index=False, engine='pyarrow')
    df_val.to_parquet(val_path, index=False, engine='pyarrow')

    size_mb = (train_path.stat().st_size + val_path.stat().st_size) / 2**20
    logging.info(f"train: {len(df_train)}条 → {train_path}")
    logging.info(f"val:   {len(df_val)}条 → {val_path}")
    logging.info(f"总大小: {size_mb:.1f}MB")


if __name__ == '__main__':
    main()
