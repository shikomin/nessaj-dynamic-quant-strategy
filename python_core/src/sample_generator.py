#!/usr/bin/env python3
"""
训练样本生成器 v3 — 多进程 + 断点续跑 + 独立文件
每只股票独立生成 → data/samples/{code}.parquet → merge_samples.py 合并

用法:
  python sample_generator.py                # 全量生成 (默认 2 workers)
  python sample_generator.py --workers 2    # 指定并行数
  python sample_generator.py --stock sh600036  # 单只股票
  python sample_generator.py --dry-run        # 仅估算
"""
import io
import sys
import time
import logging
import argparse
import traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

from config import load_config, PROJECT_ROOT
from logger import setup_logging
from td_connector import TdConnector, ALL_COLS_FEATURE, ALL_COLS_M_QUERY
from backtest_engine import run_backtest, STRATEGY_PARAMS, has_gap
from utils import disable_system_proxy

optuna = None

FEATURE_WINDOW = 240
BACKTEST_WINDOW = 480
STRIDE = 60
OUTPUT_DIR_NAME = "samples"


def _get_optuna():
    global optuna
    if optuna is None:
        import optuna as _o
        optuna = _o
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    return optuna


def _features_to_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, arr.astype(np.float32))
    return buf.getvalue()


def _generate_one_stock(code: str, td: TdConnector, n_trials: int, out_dir: Path) -> dict:
    """单只股票样本生成 → 写入 out_dir/{code}.parquet, 返回统计信息"""
    out_path = out_dir / f"{code}.parquet"

    # ── 断点续跑：已完成的跳过 ──
    if out_path.exists() and out_path.stat().st_size > 1000:
        try:
            existing = pd.read_parquet(out_path)
            if len(existing) > 0:
                logging.info(f"  {code}: 已完成 ({len(existing)}条), 跳过")
                return {'code': code, 'samples': len(existing), 'skipped': True}
        except:
            pass

    # ── 一次性加载全量数据 ──
    table_f = f"f_{code}"
    table_m = f"m_{code}"

    rows_f = td.query(f"SELECT * FROM {table_f} ORDER BY ts ASC")
    if not rows_f:
        logging.warning(f"  {code}: 无特征数据")
        return {'code': code, 'samples': 0, 'skipped': False}

    col_f = ['ts'] + ALL_COLS_FEATURE
    df_f = pd.DataFrame(rows_f, columns=col_f)
    df_f['ts'] = pd.to_datetime(df_f['ts'])
    for c in ALL_COLS_FEATURE:
        df_f[c] = pd.to_numeric(df_f[c], errors='coerce')

    rows_m = td.query(f"SELECT ts, open, high, low, close, volume FROM {table_m} ORDER BY ts ASC")
    df_m = pd.DataFrame(rows_m, columns=ALL_COLS_M_QUERY)
    df_m['ts'] = pd.to_datetime(df_m['ts'])
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df_m[c] = pd.to_numeric(df_m[c], errors='coerce')

    n_total = min(len(df_f), len(df_m))
    total_windows = max(0, (n_total - FEATURE_WINDOW - BACKTEST_WINDOW) // STRIDE + 1)
    logging.info(f"  {code}: {n_total}条 → {total_windows}个窗口")

    # ── 内存中滑动窗口 + Optuna ──
    samples = []
    chunk_files = []
    skipped_gap = 0

    def _flush_chunk():
        nonlocal samples
        if not samples:
            return
        p = out_dir / f"{code}_{len(chunk_files):04d}.tmp.parquet"
        pd.DataFrame(samples).to_parquet(p, index=False, engine='pyarrow')
        chunk_files.append(p)
        logging.debug(f"    {code}: flush {len(samples)}条 → {p.name}")
        samples.clear()

    for start in range(0, n_total - FEATURE_WINDOW - BACKTEST_WINDOW + 1, STRIDE):
        feat_end = start + FEATURE_WINDOW
        bt_end = feat_end + BACKTEST_WINDOW

        feat_arr = df_f.iloc[start:feat_end][ALL_COLS_FEATURE].values.astype(np.float32)
        bt_df = df_m.iloc[feat_end:bt_end][ALL_COLS_M_QUERY].copy()
        feat_ts = df_f.iloc[start:feat_end]['ts']

        if has_gap(feat_ts.to_frame('ts')) or has_gap(bt_df) or len(bt_df) < 60:
            skipped_gap += 1
            continue

        ot = _get_optuna()
        study = ot.create_study(direction="maximize")
        try:
            study.optimize(
                lambda trial: _objective(trial, bt_df),
                n_trials=n_trials, show_progress_bar=False
            )
        except Exception:
            continue

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
                'feature_start_ts': str(feat_ts.iloc[0]),
                'stock_code': code,
            })

        # 间歇刷盘：每 1000 条写一个 chunk 文件
        if len(samples) >= 1000:
            _flush_chunk()

    # 处理剩余 + 合并 chunk
    _flush_chunk()

    # 合并所有 chunk → 最终文件
    if chunk_files:
        dfs = []
        for p in chunk_files:
            dfs.append(pd.read_parquet(p))
        df_out = pd.concat(dfs, ignore_index=True)
    else:
        df_out = pd.DataFrame()

    if not df_out.empty:
        df_out.to_parquet(out_path, index=False, engine='pyarrow')
        # 清理 chunk 文件
        for p in chunk_files:
            p.unlink(missing_ok=True)
    else:
        pd.DataFrame().to_parquet(out_path, index=False, engine='pyarrow')

    gap_msg = f", 跳过{skipped_gap}个缺口" if skipped_gap else ""
    logging.info(f"  {code}: {len(df_out)}条样本{gap_msg} → {out_path.name}")
    return {'code': code, 'samples': len(df_out), 'skipped': False, 'gaps': skipped_gap}


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
    return result['calmar']


def _worker(args: tuple) -> dict:
    """多进程 Worker: (code, config_dict, n_trials, out_dir) → result"""
    code, config_dict, n_trials, out_dir = args

    # 子进程独立日志
    import logging as _log
    log_cfg = config_dict.get('logger', {})
    level = getattr(_log, log_cfg.get('level', 'INFO').upper(), _log.INFO)
    _log.getLogger().handlers.clear()
    _log.basicConfig(level=level, format='%(asctime)s [%(levelname)s] %(message)s')

    td = TdConnector(config_dict)
    if not td.connect():
        return {'code': code, 'samples': 0, 'skipped': False, 'error': 'TDengine 连接失败'}

    try:
        result = _generate_one_stock(code, td, n_trials, Path(out_dir))
        return result
    except Exception as e:
        _log.error(f"  {code}: Worker 异常: {e}\n{traceback.format_exc()}")
        return {'code': code, 'samples': 0, 'skipped': False, 'error': str(e)}
    finally:
        td.close()


def load_stock_list(csv_path: str) -> list[dict]:
    path = PROJECT_ROOT / csv_path
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype=str)
    return [{k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items()}
            for _, row in df.iterrows()]


def main():
    parser = argparse.ArgumentParser(description='训练样本生成 v3 (多进程)')
    parser.add_argument('--config', default=None)
    parser.add_argument('--stock')
    parser.add_argument('--workers', type=int, default=1, help='并行进程数 (默认1, 4核4G下安全)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--trials', type=int, default=50, help='Optuna trials/窗口')
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config, "sample_generator.log")
    disable_system_proxy()

    out_dir = PROJECT_ROOT / "data" / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.stock:
        stocks = [{'代码': args.stock, '名称': args.stock}]
    else:
        stocks = load_stock_list(config['data'].get('stock_list', 'stock_list.csv'))
        if not stocks:
            logging.error("股票列表为空")
            return

    logging.info("=" * 60)
    logging.info(f"样本生成 v3: {len(stocks)}只股, stride={STRIDE}, trials={args.trials}, workers={args.workers}")
    logging.info("=" * 60)

    # 连接 TDengine 估算窗口数
    td_est = TdConnector(config)
    if td_est.connect():
        if args.dry_run:
            total_est = 0
            for s in stocks:
                code = s.get('代码', '')
                try:
                    rows = td_est.query(f"SELECT COUNT(*) FROM f_{code}")
                    count = int(rows[0][0]) if rows and rows[0] and rows[0][0] else 0
                    n = max(0, (count - FEATURE_WINDOW - BACKTEST_WINDOW) // STRIDE + 1)
                    total_est += n
                except:
                    pass
            td_est.close()
            logging.info(f"预估: {total_est}窗口 × 4条 = {total_est*4}条")
            logging.info(f"预估耗时 (2 workers): ~{total_est * args.trials * 0.012 / 2 / 60:.0f}分钟")
            return
        td_est.close()

    # ── 多进程生成 ──
    # 将 config 转换为纯 dict (避免序列化问题)
    config_serializable = {
        'tdengine': config['tdengine'],
        'logger': config.get('logger', {'level': 'WARNING'}),
    }

    start_time = time.time()
    total_samples = 0
    success, failed = 0, 0

    if args.stock:
        # 单股模式 → 主进程直接跑
        td = TdConnector(config)
        if td.connect():
            try:
                r = _generate_one_stock(args.stock, td, args.trials, out_dir)
                total_samples = r['samples']
                success = 1 if r['samples'] > 0 else 0
            finally:
                td.close()
    else:
        # 多进程模式
        tasks = [(s.get('代码', ''), config_serializable, args.trials, str(out_dir))
                 for s in stocks if s.get('代码', '')]

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_worker, task): task[0] for task in tasks}

            for i, future in enumerate(as_completed(futures)):
                code = futures[future]
                try:
                    r = future.result()
                    total_samples += r.get('samples', 0)
                    if r.get('error'):
                        logging.error(f"  [{i+1}/{len(stocks)}] {code}: {r['error']}")
                        failed += 1
                    else:
                        success += 1
                except Exception as e:
                    logging.error(f"  [{i+1}/{len(stocks)}] {code}: Worker 崩溃: {e}")
                    failed += 1

    elapsed = time.time() - start_time
    logging.info("=" * 60)
    logging.info(f"样本生成完成: 成功 {success}, 失败 {failed}, {total_samples}条, 耗时 {elapsed/60:.1f}分钟")
    logging.info(f"输出目录: {out_dir}")
    logging.info("=" * 60)


if __name__ == '__main__':
    main()
