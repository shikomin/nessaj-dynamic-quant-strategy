#!/usr/bin/env python3
"""
训练样本生成器 v2
滑动窗口(stride=60) → Optuna 50 trials(5参数) → Plan B 负采样 → Parquet
支持: 停牌缺口检测, T+1(按日期), 止盈止损
"""
import io
import sys
import time
import logging
import argparse

import numpy as np
import pandas as pd

from config import load_config, PROJECT_ROOT
from logger import setup_logging
from td_connector import TdConnector, ALL_COLS_FEATURE
from backtest_engine import run_backtest, STRATEGY_PARAMS, has_gap
from utils import disable_system_proxy

optuna = None

FEATURE_WINDOW = 240
BACKTEST_WINDOW = 480
STRIDE = 60
N_POSITIVE, N_WORST, N_MID = 1, 1, 2

# 边生成边写入，避免 OOM
WRITE_CHUNK_SIZE = 1000  # 每 1000 条 flush 一次


def _get_optuna():
    global optuna
    if optuna is None:
        import optuna as _o
        optuna = _o
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    return optuna


def load_stock_list(csv_path: str) -> list[dict]:
    path = PROJECT_ROOT / csv_path
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype=str)
    return [{k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items()}
            for _, row in df.iterrows()]


def _features_to_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, arr.astype(np.float32))
    return buf.getvalue()


def generate_samples(code: str, td: TdConnector, n_trials: int = 50) -> list[dict]:
    table_f = f"f_{code}"
    table_m = f"m_{code}"

    # 读取特征
    rows_f = td.query(f"SELECT * FROM {table_f} ORDER BY ts ASC")
    if not rows_f:
        logging.warning(f"  {code}: 无特征数据")
        return []
    col_f = ['ts'] + ALL_COLS_FEATURE
    df_f = pd.DataFrame(rows_f, columns=col_f)
    df_f['ts'] = pd.to_datetime(df_f['ts'])
    for c in ALL_COLS_FEATURE:
        df_f[c] = pd.to_numeric(df_f[c], errors='coerce')

    # 读取原始K线(需要 ts 列给 T+1 日期分组)
    rows_m = td.query(f"SELECT ts, open, high, low, close, volume FROM {table_m} ORDER BY ts ASC")
    df_m = pd.DataFrame(rows_m, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    df_m['ts'] = pd.to_datetime(df_m['ts'])
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df_m[c] = pd.to_numeric(df_m[c], errors='coerce')

    n_total = min(len(df_f), len(df_m))
    total_windows = max(0, (n_total - FEATURE_WINDOW - BACKTEST_WINDOW) // STRIDE + 1)
    logging.info(f"  {code}: {n_total}条 → {total_windows}个窗口")

    samples = []
    skipped_gap = 0

    for start in range(0, n_total - FEATURE_WINDOW - BACKTEST_WINDOW + 1, STRIDE):
        feat_end = start + FEATURE_WINDOW
        bt_end = feat_end + BACKTEST_WINDOW

        feat_arr = df_f.iloc[start:feat_end][ALL_COLS_FEATURE].values.astype(np.float32)
        bt_df = df_m.iloc[feat_end:bt_end][['ts', 'open', 'high', 'low', 'close', 'volume']].copy()
        feat_ts = df_f.iloc[start:feat_end]['ts']

        # 停牌缺口检测
        if has_gap(feat_ts.to_frame('ts')) or has_gap(bt_df) or len(bt_df) < 60:
            skipped_gap += 1
            continue

        # Optuna 优化 (5 参数)
        ot = _get_optuna()
        study = ot.create_study(direction="maximize")
        study.optimize(
            lambda trial: _objective(trial, bt_df),
            n_trials=n_trials, show_progress_bar=False
        )

        # Plan B
        trials = study.trials
        sorted_trials = sorted(
            [(t.values[0] if t.values else -999, t) for t in trials if t.values],
            key=lambda x: x[0], reverse=True
        )
        if len(sorted_trials) < 4:
            continue

        best_t  = sorted_trials[0][1]
        worst_t = sorted_trials[-1][1]
        mid1_t  = sorted_trials[len(sorted_trials) // 3][1]
        mid2_t  = sorted_trials[2 * len(sorted_trials) // 3][1]

        feat_blob = _features_to_bytes(feat_arr)

        for trial, weight in [(best_t, 1.0), (worst_t, 0.1), (mid1_t, 0.3), (mid2_t, 0.3)]:
            sid = trial.user_attrs.get('strategy', 0)
            params = trial.user_attrs.get('params', [0, 0, 0, 0.05, 240])
            calmar = trial.values[0] if trial.values else 0.0
            samples.append({
                'features_blob': feat_blob,
                'strategy_label': int(sid),
                'p1': float(params[0]), 'p2': float(params[1]),
                'p3': float(params[2]), 'p4': float(params[3]) if len(params) > 3 else 0.05,
                'p5': float(params[4]) if len(params) > 4 else 240,
                'calmar': float(calmar),
                'sample_weight': float(weight),
                'feature_start_ts': str(feat_ts.iloc[0]),  # 时间戳，用于时序切分
                'stock_code': code,
            })

    if skipped_gap:
        logging.info(f"  {code}: 跳过 {skipped_gap} 个缺口窗口")
    logging.info(f"  {code}: {len(samples)}条样本")
    return samples


def _objective(trial, bt_df):
    sid = trial.suggest_categorical("strategy", [0, 1, 2, 3, 4])
    info = STRATEGY_PARAMS[sid]
    p1 = trial.suggest_float(info['p1_name'], *info['p1_range'])
    p2 = trial.suggest_float(info['p2_name'], *info['p2_range'])
    p3 = trial.suggest_float(info['p3_name'], *info['p3_range'])
    p4 = trial.suggest_float(info['p4_name'], *info['p4_range'])
    p5 = trial.suggest_float(info['p5_name'], *info['p5_range'])
    result = run_backtest(bt_df, sid, p1, p2, p3, p4, p5)
    trial.set_user_attr("strategy", sid)
    trial.set_user_attr("params", [float(p1), float(p2), float(p3), float(p4), float(p5)])
    trial.set_user_attr("result", result)
    return result['calmar']


def main():
    parser = argparse.ArgumentParser(description='训练样本生成 v2')
    parser.add_argument('--config', default=None)
    parser.add_argument('--stock')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--trials', type=int, default=50, help='Optuna trials/窗口')
    parser.add_argument('--train-ratio', type=float, default=0.8)
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config, "sample_generator.log")
    disable_system_proxy()

    td = TdConnector(config)
    if not td.connect():
        sys.exit(1)

    try:
        if args.stock:
            stocks = [{'代码': args.stock, '名称': args.stock}]
        else:
            stocks = load_stock_list(config['data'].get('stock_list', 'stock_list.csv'))
            if not stocks:
                logging.error("股票列表为空")
                return

        logging.info("=" * 60)
        logging.info(f"样本生成 v2: {len(stocks)}股, stride={STRIDE}, trials={args.trials}/窗口")
        logging.info("=" * 60)

        if args.dry_run:
            total_est = 0
            for s in stocks:
                code = s.get('代码', '')
                try:
                    rows = td.query(f"SELECT COUNT(*) FROM f_{code}")
                    count = int(rows[0][0]) if rows and rows[0] and rows[0][0] else 0
                    n = max(0, (count - FEATURE_WINDOW - BACKTEST_WINDOW) // STRIDE + 1)
                    total_est += n
                except:
                    pass
            logging.info(f"预估: {total_est}窗口 × 4条 = {total_est*4}条样本")
            logging.info(f"预估耗时: ~{total_est * args.trials * 0.012 / 60:.0f}分钟")
            return

        all_samples = []
        tmp_path = PROJECT_ROOT / "data" / "samples_tmp.parquet"
        flush_count = 0
        start_time = time.time()
        for s in stocks:
            code = s.get('代码', '')
            try:
                batch = generate_samples(code, td, args.trials)
                all_samples.extend(batch)
                # 内存保护：超过 50K 条 flush 到临时文件
                if len(all_samples) >= 50000:
                    df_tmp = pd.DataFrame(all_samples)
                    df_tmp.to_parquet(tmp_path, index=False, engine='pyarrow',
                                      append=(flush_count > 0))
                    logging.info(f"  flush {flush_count+1}: {len(all_samples)}条 → {tmp_path.name}")
                    all_samples.clear()
                    flush_count += 1
            except Exception as e:
                logging.error(f"  {code}: 样本生成失败: {e}")

        # 合并内存中的剩余 + 临时文件
        if flush_count > 0 and tmp_path.exists():
            df_tmp = pd.read_parquet(tmp_path)
            df_mem = pd.DataFrame(all_samples) if all_samples else pd.DataFrame()
            df = pd.concat([df_tmp, df_mem], ignore_index=True)
            tmp_path.unlink()
        else:
            df = pd.DataFrame(all_samples) if all_samples else pd.DataFrame()

        elapsed = time.time() - start_time
        if df.empty:
            logging.error("无样本生成")
            return

        logging.info(f"生成 {len(df)} 条样本, 耗时 {elapsed:.0f}s")

        # 按时间顺序排列，前80%训练，后20%验证（时序铁律）
        df['_sort_ts'] = pd.to_datetime(df['feature_start_ts'])
        df = df.sort_values('_sort_ts').drop(columns=['_sort_ts']).reset_index(drop=True)
        n_train = int(len(df) * args.train_ratio)

        train_path = PROJECT_ROOT / "data" / "train.parquet"
        val_path   = PROJECT_ROOT / "data" / "val.parquet"
        df[:n_train].to_parquet(train_path, index=False, engine='pyarrow')
        df[n_train:].to_parquet(val_path, index=False, engine='pyarrow')

        size_mb = (train_path.stat().st_size + val_path.stat().st_size) / 2**20
        logging.info(f"train: {n_train}条, val: {len(df)-n_train}条, {size_mb:.1f}MB")

    finally:
        td.close()


if __name__ == '__main__':
    main()
