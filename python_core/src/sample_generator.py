#!/usr/bin/env python3
"""
训练样本生成器 v3 — 多进程 + 断点续跑 + 独立文件

===================================================================
核心理念: 把"哪个策略 + 哪组参数最适合当前行情"这个问题转化为
监督学习的标签。模型看到前 1 天的行情特征 → 预测后 2 天的
最优策略和参数。

===================================================================
Pipeline (单只股票)
--------------------
1. 从 TDengine 加载全量 26 维特征 (f_{code}) 和原始 K 线 (m_{code})
2. 滑动窗口: 步长 60 条 (1小时), 特征窗口 240 条 (1天), 回测窗口 480 条 (2天)
3. 每个窗口跑 Optuna 50 trials, 搜索 (策略+参数) → 选 best/worst/2mid
4. Plan B 负采样: 每个窗口产 4 条记录 (1正×1.0 + 3负×加权)
5. 间歇刷盘: 每 1000 条写一个临时 chunk
6. 合并 chunk → 最终 data/samples/{code}.parquet

===================================================================
Plan B 负采样逻辑
-------------------
不是只告诉模型"什么策略好"(正样本), 还要告诉它"什么策略差"(负样本)。
这样模型学到的不是精确参数值, 而是策略好坏之间的相对排序。

| 样本类型 | 来源                   | 权重  | 作用     |
|----------|------------------------|-------|----------|
| 正样本   | Calmar 最高的 trial    | 1.0   | 这种行情这么做 |
| 差负样本 | Calmar 最低的 trial    | 0.1   | 千万避开这个   |
| 中负样本 | 随机中等 Calmar trials | 0.3   | 丰富负样本     |

===================================================================
多进程设计
-----------
每个 Worker 进程独立连接 TDengine, 处理一只股票。
优点:
- Python GIL 不阻塞 (TDengine I/O 和 Optuna 计算交替)
- 单进程挂了不影响其他
- 子进程处理完即释放内存

缺点:
- 每个进程都需要全量加载数据 (约 10MB/进程)
- 4核4G 机器建议 workers=1~2

===================================================================
内存模型 (单只股票)
-------------------
- df_f (特征): 6万行 × 26列 float32 ≈ 6.2 MB
- df_m (原始K线): 6万行 × 6列 float64 ≈ 2.9 MB
- samples list (1000窗口 × 4条): ~100 MB (features_blob 占大头)
- Optuna study 对象: ~20 MB

总计单股票峰值 ≈ 130 MB。如果 features_blob 改为共享索引, 可以降到 ~30 MB。

用法:
  python sample_generator.py                # 全量生成 (默认 1 worker)
  python sample_generator.py --workers 2    # 指定并行数
  python sample_generator.py --stock sh600036  # 单只股票测试
  python sample_generator.py --dry-run        # 仅估算不执行
  python sample_generator.py --trials 20      # 自定义 trials 数
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

# 将项目根目录加入 sys.path, 使 components/ 和 utils/ 模块可被导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components.config import load_config, PROJECT_ROOT
from components.logger import setup_logging
from components.td_connector import TdConnector, ALL_COLS_FEATURE, ALL_COLS_M_QUERY
from backtest_engine import run_backtest, STRATEGY_PARAMS, has_gap
from utils.proxy_utils import disable_system_proxy
from utils.data_utils import load_stock_list

# ── Optuna 延迟导入 (子进程里才导，主进程不需要) ──
optuna = None

# ============================================================
# 核心超参数
# ============================================================

FEATURE_WINDOW = 240    # 特征窗口: 240条 = 1个交易日 (240 分钟)
BACKTEST_WINDOW = 480   # 回测窗口: 480条 = 2个交易日。**Calmar低的主因: 2天+ T+1只能做1笔交易**
STRIDE = 60             # 步长: 60条 = 1小时。相邻窗口特征重叠180条, 但回测窗口(标签)不重叠
OUTPUT_DIR_NAME = "samples"  # 输出目录名


# ============================================================
# 辅助函数
# ============================================================

def _get_optuna():
    """
    延迟导入 Optuna (避免主进程不必要时加载)。
    使用全局变量缓存，只导入一次。
    """
    global optuna
    if optuna is None:
        import optuna as _o
        optuna = _o
        # 关闭 Optuna 自己的日志输出 (WARNING 级别)，避免刷屏
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    return optuna


def _features_to_bytes(arr: np.ndarray) -> bytes:
    """
    将特征数组序列化为 bytes 用于 parquet 存储。
    存储为 float32 减少空间: 240×26×4 = 24960 字节 ≈ 25KB/window。

    ⚠ 问题: 同一个窗口的 4 条样本 (正+负) 存储了 4 份相同的特征,
    浪费了 75% 的特征存储空间。优化方案见 optimization_plan.md 3.2 节。
    """
    buf = io.BytesIO()
    np.save(buf, arr.astype(np.float32))
    return buf.getvalue()


# ============================================================
# 单只股票样本生成 (主逻辑)
# ============================================================

def _generate_one_stock(code: str, td: TdConnector, n_trials: int, out_dir: Path) -> dict:
    """
    为单只股票生成所有样本 → 写入 out_dir/{code}.parquet。

    流程:
    1. 断点续跑: 如果输出文件已存在且非空, 跳过
    2. 一次性从 TDengine 加载全量特征 + 原始K线 → 内存
    3. 滑动窗口循环:
       a. 取 feat_arr = df_f[start:start+240][26维]
       b. 取 bt_df   = df_m[start+240:start+720][OHLCV]
       c. 缺口检测 → 跳过
       d. Optuna 50 trials 搜索 (策略 + 参数)
       e. Plan B: 选 best/worst/2mid → 4条样本
       f. 每 1000 条样本刷盘 (防止内存爆炸)
    4. 合并所有 chunk → 最终 parquet 文件
    5. 清理临时 chunk 文件

    返回: dict {code, samples, skipped, gaps}
    """
    out_path = out_dir / f"{code}.parquet"

    # ── 断点续跑: 已完成的股票跳过 ──
    if out_path.exists() and out_path.stat().st_size > 1000:
        try:
            existing = pd.read_parquet(out_path)
            if len(existing) > 0:
                logging.info(f"  {code}: 已完成 ({len(existing)}条), 跳过")
                return {'code': code, 'samples': len(existing), 'skipped': True}
        except:
            pass

    # ════════════════════════════════════════════════════════════
    # 步骤 1: 从 TDengine 一次性加载全量数据
    # ════════════════════════════════════════════════════════════

    # 表名规则: f_{code} = 特征表, m_{code} = 原始1分钟K线表
    table_f = f"f_{code}"
    table_m = f"m_{code}"

    # ── 加载 26 维特征数据 ──
    rows_f = td.query(f"SELECT * FROM {table_f} ORDER BY ts ASC")
    if not rows_f:
        logging.warning(f"  {code}: 无特征数据")
        return {'code': code, 'samples': 0, 'skipped': False}

    col_f = ['ts'] + ALL_COLS_FEATURE
    df_f = pd.DataFrame(rows_f, columns=col_f)
    df_f['ts'] = pd.to_datetime(df_f['ts'])
    for c in ALL_COLS_FEATURE:
        df_f[c] = pd.to_numeric(df_f[c], errors='coerce')

    # ── 加载原始 1分钟 K 线数据 (用于回测) ──
    rows_m = td.query(f"SELECT ts, open, high, low, close, volume FROM {table_m} ORDER BY ts ASC")
    df_m = pd.DataFrame(rows_m, columns=ALL_COLS_M_QUERY)
    df_m['ts'] = pd.to_datetime(df_m['ts'])
    for c in ['open', 'high', 'low', 'close', 'volume']:
        df_m[c] = pd.to_numeric(df_m[c], errors='coerce')

    # ════════════════════════════════════════════════════════════
    # 步骤 2: 滑动窗口循环
    # ════════════════════════════════════════════════════════════

    n_total = min(len(df_f), len(df_m))
    # 窗口总数计算公式: 只要够放(特征窗口+回测窗口), 每 STRIDE 条取一个窗口
    total_windows = max(0, (n_total - FEATURE_WINDOW - BACKTEST_WINDOW) // STRIDE + 1)
    logging.info(f"  {code}: {n_total}条 → {total_windows}个窗口")

    if total_windows == 0:
        # 数据不足 (比如只有 300 条, 不够 240+480=720), 直接返回
        logging.warning(f"  {code}: 数据不足 (需要至少 {FEATURE_WINDOW + BACKTEST_WINDOW}条)")
        return {'code': code, 'samples': 0, 'skipped': False}

    samples = []       # 内存中的样本缓冲区
    chunk_files = []   # 已刷盘的临时文件列表
    skipped_gap = 0    # 因缺口跳过的窗口计数

    def _flush_chunk():
        """
        将内存中的样本刷到临时 parquet 文件。
        防止 OOM: 当 samples 累积到 1000 条时触发。
        """
        nonlocal samples
        if not samples:
            return
        # 命名规则: {code}_{序号}.tmp.parquet
        p = out_dir / f"{code}_{len(chunk_files):04d}.tmp.parquet"
        pd.DataFrame(samples).to_parquet(p, index=False, engine='pyarrow')
        chunk_files.append(p)
        logging.debug(f"    {code}: flush {len(samples)}条 → {p.name}")
        samples.clear()

    # ── 主循环: 按 STRIDE (60条=1小时) 滑动 ──
    for start in range(0, n_total - FEATURE_WINDOW - BACKTEST_WINDOW + 1, STRIDE):
        feat_end = start + FEATURE_WINDOW                      # 特征窗口结束位置
        bt_end = feat_end + BACKTEST_WINDOW                    # 回测窗口结束位置

        # feat_arr: (240, 26) float32 特征矩阵 → 模型输入
        feat_arr = df_f.iloc[start:feat_end][ALL_COLS_FEATURE].values.astype(np.float32)

        # bt_df: 回测用的原始K线 → 回测引擎输入
        bt_df = df_m.iloc[feat_end:bt_end][ALL_COLS_M_QUERY].copy()
        feat_ts = df_f.iloc[start:feat_end]['ts']

        # ── 停牌/缺口检测: 特征窗口和回测窗口都要检查 ──
        if has_gap(feat_ts.to_frame('ts')) or has_gap(bt_df) or len(bt_df) < 60:
            skipped_gap += 1
            continue

        # ════════════════════════════════════════════════════════
        # 步骤 3: Optuna 搜索最优 (策略, 参数) 组合
        # ════════════════════════════════════════════════════════
        # 在当前回测窗口内, 随机尝试 50 组 (策略, 参数), 找到 Calmar 最高的
        # ⚠ 问题: 50 trials 混合搜索 5 策略, 每个策略平均只分到 ~10 trials,
        #   对 5 维连续参数空间来说完全不够。改进见 optimization_plan.md 2.3 节。

        ot = _get_optuna()
        study = ot.create_study(direction="maximize")   # 最大化 Calmar Ratio
        try:
            study.optimize(
                lambda trial: _objective(trial, bt_df),
                n_trials=n_trials, show_progress_bar=False
            )
        except Exception:
            continue   # 单个窗口失败不影响整体

        # ── 获取所有 trial 并按 Calmar 排序 ──
        trials = study.trials
        sorted_trials = sorted(
            [(t.values[0] if t.values else -999, t) for t in trials if t.values],
            key=lambda x: x[0], reverse=True
        )
        if len(sorted_trials) < 4:
            continue   # 至少需要 4 个有效 trial 才能做 Plan B 采样

        # ════════════════════════════════════════════════════════
        # 步骤 4: Plan B 负采样 → 每个窗口产 4 条训练记录
        # ════════════════════════════════════════════════════════
        best_t  = sorted_trials[0][1]                            # Calmar 最高 = 正样本 (1.0)
        worst_t = sorted_trials[-1][1]                           # Calmar 最低 = 差负样本 (0.1)
        mid1_t  = sorted_trials[len(sorted_trials) // 3][1]     # 中等 trial A (0.3)
        mid2_t  = sorted_trials[2 * len(sorted_trials) // 3][1] # 中等 trial B (0.3)

        # features_blob: 将特征矩阵序列化为 bytes 存入 parquet
        # 训练时用 np.load(BytesIO(blob)) 还原
        feat_blob = _features_to_bytes(feat_arr)

        for trial, weight in [(best_t, 1.0), (worst_t, 0.1), (mid1_t, 0.3), (mid2_t, 0.3)]:
            # 从 trial 的 user_attrs 中取出 Optuna 搜索到的策略和参数
            sid = trial.user_attrs.get('strategy', 0)
            params = trial.user_attrs.get('params', [0, 0, 0, 0.05, 240])
            calmar = trial.values[0] if trial.values else 0.0

            # 构造一条样本记录 (对应 parquet 的一行)
            samples.append({
                'features_blob': feat_blob,        # 序列化的 (240, 26) 特征矩阵
                'strategy_label': int(sid),        # 策略 ID (0-4): 模型分类头要预测的目标
                'p1': float(params[0]),            # 参数 1: 策略专属入场信号参数
                'p2': float(params[1]),            # 参数 2: 策略专属入场信号参数
                'p3': float(params[2]),            # 参数 3: stop_atr 止损倍数
                'p4': float(params[3]) if len(params) > 3 else 0.05,  # 参数 4: 止盈百分比
                'p5': float(params[4]) if len(params) > 4 else 240,   # 参数 5: 最大持仓分钟
                'calmar': float(calmar),            # Calmar 值 (用于训练时加权)
                'sample_weight': float(weight),     # 样本权重: 1.0/0.3/0.1
                'feature_start_ts': str(feat_ts.iloc[0]),  # 特征窗口起始时间 (用于时序排序)
                'stock_code': code,                          # 股票代码
            })

        # 间歇刷盘: 每 1000 条样本写一个 chunk 文件 (防止内存爆炸)
        if len(samples) >= 1000:
            _flush_chunk()

    # ════════════════════════════════════════════════════════════
    # 步骤 5: 处理剩余样本 + 合并所有 chunk
    # ════════════════════════════════════════════════════════════
    _flush_chunk()   # 刷出最后一批残留样本

    # 合并所有临时 chunk 文件 → 最终 parquet
    if chunk_files:
        dfs = []
        for p in chunk_files:
            dfs.append(pd.read_parquet(p))
        df_out = pd.concat(dfs, ignore_index=True)
    else:
        # 没有 chunk 文件: 要么全在内存里, 要么一个样本都没生成
        df_out = pd.DataFrame(samples) if samples else pd.DataFrame()

    if not df_out.empty:
        df_out.to_parquet(out_path, index=False, engine='pyarrow')
        # 清理临时 chunk 文件
        for p in chunk_files:
            p.unlink(missing_ok=True)
    else:
        # 创建一个空的 parquet 文件作为断点续跑的标记
        pd.DataFrame().to_parquet(out_path, index=False, engine='pyarrow')

    gap_msg = f", 跳过{skipped_gap}个缺口" if skipped_gap else ""
    logging.info(f"  {code}: {len(df_out)}条样本{gap_msg} → {out_path.name}")
    return {'code': code, 'samples': len(df_out), 'skipped': False, 'gaps': skipped_gap}


# ============================================================
# Optuna 目标函数
# ============================================================

def _objective(trial, bt_df):
    """
    Optuna 的目标函数。每次 trial 随机选择一个策略 + 一组参数,
    跑回测后返回 Calmar Ratio。

    ⚠ 问题: 当前实现是在 5 个策略中随机选 (categorical), 导致每个策略
    被采样的次数不确定。改进方案: 外部循环固定策略, 每个策略独立 study。
    """
    # ── 随机选择策略 ──
    sid = trial.suggest_categorical("strategy", [0, 1, 2, 3, 4])

    # ── 在该策略的参数范围内随机采样 ──
    info = STRATEGY_PARAMS[sid]
    p1 = trial.suggest_float(info['p1_name'], *info['p1_range'])   # 策略专属参数1
    p2 = trial.suggest_float(info['p2_name'], *info['p2_range'])   # 策略专属参数2
    p3 = trial.suggest_float(info['p3_name'], *info['p3_range'])   # stop_atr
    p4 = trial.suggest_float(info['p4_name'], *info['p4_range'])   # profit_pct
    p5 = trial.suggest_float(info['p5_name'], *info['p5_range'])   # max_hold

    # ── 执行回测 ──
    result = run_backtest(bt_df, sid, p1, p2, p3, p4, p5)

    # ── 保存 trial 信息 (用于后续取 best/worst) ──
    trial.set_user_attr("strategy", sid)
    trial.set_user_attr("params", [float(p1), float(p2), float(p3), float(p4), float(p5)])

    return result['calmar']   # 最大化 Calmar Ratio


# ============================================================
# 多进程 Worker
# ============================================================

def _worker(args: tuple) -> dict:
    """
    多进程 Worker 入口函数。

    每个 Worker 进程:
    1. 独立配置日志 (不共享主进程 logger)
    2. 独立连接 TDengine (不共享连接)
    3. 调用 _generate_one_stock 处理一只股票
    4. 返回统计字典

    参数 args = (code, config_dict, n_trials, out_dir)
    """
    code, config_dict, n_trials, out_dir = args

    # ── 子进程独立日志配置 ──
    import logging as _log
    log_cfg = config_dict.get('logger', {})
    level = getattr(_log, log_cfg.get('level', 'INFO').upper(), _log.INFO)
    _log.getLogger().handlers.clear()
    _log.basicConfig(level=level, format='%(asctime)s [%(levelname)s] %(message)s')

    # ── 独立 TDengine 连接 ──
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


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='训练样本生成 v3 (多进程)')
    parser.add_argument('--config', default=None, help='配置文件路径')
    parser.add_argument('--stock', help='单只股票代码 (如 sh600036)')
    parser.add_argument('--workers', type=int, default=1, help='并行进程数 (默认1, 4核4G下推荐不超过2)')
    parser.add_argument('--dry-run', action='store_true', help='仅估算窗口数和耗时，不实际生成')
    parser.add_argument('--trials', type=int, default=50, help='每个窗口的 Optuna trials 数 (默认50，临时测试可用5-10)')
    args = parser.parse_args()

    # ── 初始化配置和日志 ──
    config = load_config(args.config)
    setup_logging(config, "sample_generator.log")
    disable_system_proxy()

    # ── 创建输出目录 ──
    out_dir = PROJECT_ROOT / "data" / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 确定股票列表 ──
    if args.stock:
        stocks = [{'代码': args.stock, '名称': args.stock}]
    else:
        stocks = load_stock_list(config['data'].get('stock_list', 'stock_list.csv'))
        if not stocks:
            logging.error("股票列表为空")
            return

    logging.info("=" * 60)
    logging.info(f"样本生成 v3: {len(stocks)}只股, stride={STRIDE}, "
                 f"窗口={FEATURE_WINDOW}/{BACKTEST_WINDOW}, trials={args.trials}, workers={args.workers}")
    logging.info("=" * 60)

    # ── Dry-run 模式: 仅估算不执行 ──
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
            logging.info(f"预估: {total_est}窗口 × 4条 = {total_est*4}条样本")
            logging.info(f"预估耗时 ({args.workers} workers): "
                         f"~{total_est * args.trials * 0.012 / args.workers / 60:.0f}分钟")
            return
        td_est.close()

    # ════════════════════════════════════════════════════════════
    # 模式 1: 单股模式 (主进程直接跑, 用于调试)
    # ════════════════════════════════════════════════════════════
    if args.stock:
        td = TdConnector(config)
        if td.connect():
            try:
                r = _generate_one_stock(args.stock, td, args.trials, out_dir)
                total_samples = r['samples']
                success = 1 if r['samples'] > 0 else 0
            finally:
                td.close()

    # ════════════════════════════════════════════════════════════
    # 模式 2: 多进程模式
    # ════════════════════════════════════════════════════════════
    else:
        # 将 config 转为纯 dict (避免 ProcessPoolExecutor 序列化问题)
        config_serializable = {
            'tdengine': config['tdengine'],
            'logger': config.get('logger', {'level': 'WARNING'}),
        }

        # 构造任务列表: 每只股票一个任务
        tasks = [(s.get('代码', ''), config_serializable, args.trials, str(out_dir))
                 for s in stocks if s.get('代码', '')]

        start_time = time.time()
        total_samples = 0
        success, failed = 0, 0

        # 启动进程池, 提交所有任务
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            # future → code 的映射
            futures = {executor.submit(_worker, task): task[0] for task in tasks}

            # 按完成顺序收集结果 (as_completed, 不是提交顺序)
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
