#!/usr/bin/env python3
"""
训练样本生成器 v3.1 — 随机搜索 + 权重标签

===================================================================
v3.1 核心改动
-------------
1. 随机搜索替代 Optuna: 每窗口 100 次独立并行回测
2. 权重标签: softmax(Calmar × 2) → 9 维策略权重向量
3. Plan B 负采样: 正(权重向量+最优参数) + 3负(扰动权重+差参数)
4. 单进程 + 线程池 + TDengine 连接池, 4核4G 环境安全

===================================================================
标注流程 (每窗口)
------------------
1. 随机采样 100 组 (策略ID, p1-p2, 通用参数6)
2. 10 线程并行跑 100 次回测 → 每个策略最高 Calmar: C₀..C₈
3. 权重标签 = softmax([C₀..C₈] × 2)   (T=0.5)
4. Plan B: 每条样本存 features_blob + weight_label

===================================================================
用法
----
  python sample_generator.py                  # 全量
  python sample_generator.py --stock sh600036 # 单股
  python sample_generator.py --dry-run        # 估算
"""
import io, sys, time, logging, argparse, queue, random, traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components.config import load_config, PROJECT_ROOT
from components.logger import setup_logging
from components.td_connector import TdConnector, ALL_COLS_M_QUERY
from backtest_engine import (run_backtest, STRATEGY_PARAMS, COMMON_PARAMS, has_gap,
                             BACKTEST_WARMUP, BACKTEST_WINDOW)
from utils.proxy_utils import disable_system_proxy
from utils.data_utils import load_stock_list

# ============================================================
# 超参数
# ============================================================
FEATURE_WINDOW   = 240
STRIDE           = 240
OUTPUT_DIR       = "samples"
CONN_POOL_SIZE   = 6
NEG_SAMPLE_COUNT = 4           # Plan B: 每窗口 4 条 (1正+3负)
WRITE_CHUNK      = 1000
RANDOM_TRIALS    = 100         # 每窗口总采样数
THREADS          = 10          # 随机搜索并行线程数
N_STRATEGIES     = 9           # 策略数 (不含空仓)
N_DIM            = 10          # 标签维度 (9策略 + 1空仓)
TRIALS_PER_SID   = 11          # 每策略分层采样数 (9×11=99, +1随机=100)


# ============================================================
# TDengine 连接池
# ============================================================
class TdConnectionPool:
    def __init__(self, config: dict, size: int = CONN_POOL_SIZE):
        self._pool = queue.Queue(maxsize=size)
        for i in range(size):
            td = TdConnector(config)
            if td.connect(): self._pool.put(td)

    def get(self) -> TdConnector: return self._pool.get()
    def put(self, td: TdConnector): self._pool.put(td)
    def close_all(self):
        while not self._pool.empty():
            try: self._pool.get_nowait().close()
            except: pass


# ============================================================
# 随机采样 + 权重标签
# ============================================================

def _random_sample_params_for(sid: int):
    """对指定策略随机采样一组 (p1, p2, + 6通用参数)。"""
    info = STRATEGY_PARAMS[sid]
    p1 = random.uniform(*info['p1_range'])
    p2 = random.uniform(*info['p2_range'])
    kwargs = {
        'stop_atr':   random.uniform(*COMMON_PARAMS['stop_atr']),
        'profit_pct': random.uniform(*COMMON_PARAMS['profit_pct']),
        'max_hold':   random.randint(*COMMON_PARAMS['max_hold']),
        'buy_ratio':  random.uniform(*COMMON_PARAMS['buy_ratio']),
        'sell_ratio': random.uniform(*COMMON_PARAMS['sell_ratio']),
        'cooling':    random.randint(*COMMON_PARAMS['cooling']),
    }
    return p1, p2, kwargs


def _compute_weights(
    all_calmars: list[float], best_by_sid: dict[int, float],
) -> np.ndarray:
    """
    rank-based → 10 维权重 (9策略 + 1空仓)。

    算法:
      1. 对所有 trial 的 Calmar 排序
      2. 每策略取其最佳 Calmar + 空仓(0) → 计算在全量中的百分位 rank
      3. softmax(rank * 3) → τ=0.33
      4. 返回 shape (10,) 权重向量
    """
    best = np.zeros(N_DIM)
    for sid in range(N_STRATEGIES):
        best[sid] = best_by_sid.get(sid, 0.0)
    best[N_DIM - 1] = 0.0  # 空仓

    all_sorted = sorted(all_calmars, reverse=True)
    if not all_sorted:
        return np.ones(N_DIM) / N_DIM

    ranks = np.zeros(N_DIM, dtype=np.float64)
    for i in range(N_DIM):
        ranks[i] = (sum(1 for x in all_sorted if x <= best[i]) / len(all_sorted))
    ranks = np.maximum(ranks, 0.001)

    tau = 0.33
    logits = ranks / tau
    logits -= logits.max()
    exp = np.exp(logits)
    return (exp / exp.sum()).astype(np.float32)


# ============================================================
# 单只股票
# ============================================================

def _features_to_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO(); np.save(buf, arr.astype(np.float32)); return buf.getvalue()


def _generate_one_stock(code: str, td_pool: TdConnectionPool, out_dir: Path) -> dict:
    out_path = out_dir / f"{code}.parquet"
    if out_path.exists() and out_path.stat().st_size > 1000:
        try:
            if len(pd.read_parquet(out_path)) > 0:
                logging.info(f"  {code}: 已完成, 跳过"); return {'code':code,'samples':len(pd.read_parquet(out_path)),'skipped':True}
        except: pass

    td = td_pool.get()
    try:
        from components.td_connector import ALL_COLS_FEATURE_V2
        rows_f = td.query(f"SELECT * FROM f_{code} ORDER BY ts ASC")
        if not rows_f:
            logging.warning(f"  {code}: 无特征"); return {'code':code,'samples':0,'skipped':False}
        col_f = ['ts']+ALL_COLS_FEATURE_V2
        df_f = pd.DataFrame(rows_f, columns=col_f); df_f['ts'] = pd.to_datetime(df_f['ts'])
        for c in ALL_COLS_FEATURE_V2: df_f[c] = pd.to_numeric(df_f[c], errors='coerce')
        rows_m = td.query(f"SELECT ts,open,high,low,close,volume FROM m_{code} ORDER BY ts ASC")
        df_m = pd.DataFrame(rows_m, columns=ALL_COLS_M_QUERY); df_m['ts'] = pd.to_datetime(df_m['ts'])
        for c in ['open','high','low','close','volume']: df_m[c] = pd.to_numeric(df_m[c], errors='coerce')
    finally:
        td_pool.put(td)

    total_needed = FEATURE_WINDOW + BACKTEST_WARMUP + BACKTEST_WINDOW
    n_total = min(len(df_f), len(df_m))
    total_windows = max(0, (n_total - total_needed) // STRIDE + 1)
    logging.info(f"  {code}: {n_total}条 → {total_windows}个窗口")
    if total_windows == 0:
        logging.warning(f"  {code}: 数据不足"); return {'code':code,'samples':0,'skipped':False}

    all_samples = []; skipped_gap = 0; calmar_vals = []

    for win_idx, start in enumerate(range(0, n_total - total_needed + 1, STRIDE)):
        feat_end = start + FEATURE_WINDOW
        bt_end   = feat_end + BACKTEST_WARMUP + BACKTEST_WINDOW
        feat_arr = df_f.iloc[start:feat_end][ALL_COLS_FEATURE_V2].values.astype(np.float32)
        feat_ts  = df_f.iloc[start:feat_end]['ts']
        bt_df    = df_m.iloc[feat_end:bt_end][ALL_COLS_M_QUERY].copy()

        if has_gap(feat_ts.to_frame('ts')) or has_gap(bt_df) or len(bt_df) < total_needed-FEATURE_WINDOW:
            skipped_gap += 1; continue

        # ── 分层采样: 每策略 11 trials, 总共 9×11=99 + 1随机=100 ──
        all_calmars_by_sid = {sid: [] for sid in range(N_STRATEGIES)}
        best_per_sid = {}       # {sid: (calmar, params_dict)}
        results_q = []

        # 策略专属 trail 函数
        def _trial_for(sid):
            p1, p2, kw = _random_sample_params_for(sid)
            r = run_backtest(bt_df, sid, p1, p2, stock_code=code, **kw)
            return {'sid': sid, 'p1': p1, 'p2': p2, 'calmar': r['calmar'], **kw}

        # 构造 99 个分层任务 + 1 个随机任务
        tasks = [(sid,) for sid in range(N_STRATEGIES) for _ in range(TRIALS_PER_SID)]
        tasks.append((random.randint(0, N_STRATEGIES - 1),))  # +1 随机

        with ThreadPoolExecutor(max_workers=THREADS) as ex:
            futures = {ex.submit(_trial_for, t[0]): t[0] for t in tasks}
            for f in as_completed(futures):
                try: results_q.append(f.result())
                except: pass

        for t in results_q:
            sid = t['sid']; calmar = t['calmar']
            all_calmars_by_sid[sid].append(calmar)
            if sid not in best_per_sid or calmar > best_per_sid[sid][0]:
                best_per_sid[sid] = (calmar, t)

        if len(best_per_sid) < 3:
            continue

        # ── rank-based 权重标签 (10维: 9策略+1空仓) ──
        all_calmars_flat = [c for v in all_calmars_by_sid.values() for c in v]
        calmar_only = {sid: v[0] for sid, v in best_per_sid.items()}
        weights = _compute_weights(all_calmars_flat, calmar_only)

        # ── Plan B ──
        feat_blob = _features_to_bytes(feat_arr)
        feat_start = str(feat_ts.iloc[0])

        # 正样本: 权重标签
        all_samples.append({
            'features_blob': feat_blob, 'weight_label': weights.tolist(),
            'calmar': float(max(v[0] for v in best_per_sid.values())),
            'sample_weight': 1.0, 'feature_start_ts': feat_start, 'stock_code': code,
        })
        calmar_vals.append(max(v[0] for v in best_per_sid.values()))

        # 负样本 ×3: 随机扰动权重
        for sw in [0.1, 0.3, 0.3]:
            noisy = weights.copy()
            noise = np.random.randn(N_DIM) * 0.1
            noisy = np.abs(noisy + noise)
            noisy /= noisy.sum()
            all_samples.append({
                'features_blob': feat_blob, 'weight_label': noisy.tolist(),
                'calmar': float(np.mean(all_calmars_flat)) if all_calmars_flat else 0.0,
                'sample_weight': sw, 'feature_start_ts': feat_start, 'stock_code': code,
            })

        if len(all_samples) >= WRITE_CHUNK:
            _flush(out_path, all_samples); all_samples.clear()

        if (win_idx+1) % 50 == 0 and calmar_vals:
            recent = calmar_vals[-50:]
            logging.info(f"    [{win_idx+1}/{total_windows}] Calmar均值:{np.mean(recent):.2f}, >0:{np.mean(np.array(recent)>0)*100:.0f}%")

    _flush(out_path, all_samples)
    try: final_df = pd.read_parquet(out_path); cnt = len(final_df)
    except: cnt = len(all_samples)
    gap_msg = f", 跳过{skipped_gap}缺口" if skipped_gap else ""
    logging.info(f"  {code}: {cnt}条样本{gap_msg}")
    cs = {'mean': float(np.mean(calmar_vals)) if calmar_vals else 0,
          'pct_pos': float(np.mean(np.array(calmar_vals)>0)) if calmar_vals else 0}
    return {'code':code, 'samples':cnt, 'skipped':False, 'gaps':skipped_gap, 'calmar_stats':cs}


def _flush(path, samples):
    if not samples: return
    df = pd.DataFrame(samples)
    if path.exists():
        try:
            old = pd.read_parquet(path)
            df = pd.concat([old, df], ignore_index=True)
        except: pass
    df.to_parquet(path, index=False, engine='pyarrow')


# ============================================================
# 质量评估
# ============================================================
def _print_report(results):
    total = sum(r['samples'] for r in results if not r.get('skipped'))
    calmar_means = [r['calmar_stats']['mean'] for r in results if r.get('calmar_stats')]
    positives = [r['calmar_stats']['pct_pos'] for r in results if r.get('calmar_stats')]
    logging.info("="*60)
    logging.info(f"样本质量: {total}条")
    if calmar_means:
        logging.info(f"窗口Calmar均值: {np.mean(calmar_means):.2f}")
        logging.info(f"Calmar>0占比: {np.mean(positives)*100:.1f}%")
    if positives:
        p = np.mean(positives)
        logging.info(f"{'可训练' if p>0.15 else '质量不足,建议调整窗口/策略'}")


# ============================================================
# main
# ============================================================
def main():
    p = argparse.ArgumentParser(description='样本生成 v3.1 (随机搜索+权重标签)')
    p.add_argument('--config', default=None); p.add_argument('--stock')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    config = load_config(args.config); setup_logging(config, "sample_generator.log")
    disable_system_proxy()
    out_dir = PROJECT_ROOT/"data"/OUTPUT_DIR; out_dir.mkdir(parents=True, exist_ok=True)
    if args.stock: stocks = [{'代码':args.stock,'名称':args.stock}]
    else:
        stocks = load_stock_list(config['data'].get('stock_list','stock_list.csv'))
        if not stocks: logging.error("empty"); return
    logging.info("="*60)
    logging.info(f"v3.1: {len(stocks)}股 trials={RANDOM_TRIALS} 窗口={FEATURE_WINDOW}/{BACKTEST_WARMUP}+{BACKTEST_WINDOW}")
    logging.info("="*60)
    if args.dry_run:
        td = TdConnector(config)
        if td.connect():
            total=0
            for s in stocks:
                try:
                    rows=td.query(f"SELECT COUNT(*) FROM f_{s['代码']}")
                    c=int(rows[0][0]) if rows and rows[0] else 0
                    total+=max(0,(c-FEATURE_WINDOW-BACKTEST_WARMUP-BACKTEST_WINDOW)//STRIDE+1)
                except: pass
            td.close()
            logging.info(f"预估: {total}窗口 ×4={total*4}条 ~{total*RANDOM_TRIALS*0.008/THREADS/60:.0f}分钟"); return
    pool = TdConnectionPool(config)
    results=[]; t0=time.time()
    for i,s in enumerate(stocks):
        code=s.get('代码','')
        try: results.append(_generate_one_stock(code,pool,out_dir))
        except Exception as e: logging.error(f"{code}: {e}")
    elapsed=time.time()-t0
    total=sum(r.get('samples',0) for r in results)
    logging.info(f"完成: {total}条 耗时{elapsed/60:.1f}分")
    if total>0: _print_report(results)
    pool.close_all()

if __name__=='__main__': main()
