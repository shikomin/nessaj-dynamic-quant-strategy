#!/usr/bin/env python3
"""合并分样本 → 时序排序 → train/val 切分 v3.1"""
import sys, logging, argparse; from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from components.config import load_config, PROJECT_ROOT; from components.logger import setup_logging

def main():
    p=argparse.ArgumentParser(description='合并样本 v3.1')
    p.add_argument('--config',default=None); p.add_argument('--train-ratio',type=float,default=0.8)
    p.add_argument('--samples-dir',default=None); args=p.parse_args()
    config=load_config(args.config) if args.config else {}; setup_logging(config,"merge_samples.log")
    d=Path(args.samples_dir) if args.samples_dir else PROJECT_ROOT/"data"/"samples"
    if not d.exists(): logging.error(f"no dir: {d}"); sys.exit(1)
    files=list(d.glob("*.parquet"))
    if not files: logging.error("empty"); sys.exit(1)
    logging.info(f"{len(files)} files")
    dfs=[]; ok=0
    for f in files:
        try:
            df=pd.read_parquet(f)
            if not df.empty and 'feature_start_ts' in df.columns: dfs.append(df); ok+=1
            else: logging.warning(f"skip empty: {f.name}")
        except: logging.warning(f"fail: {f.name}")
    if not dfs: logging.error("no valid"); sys.exit(1)
    all_df=pd.concat(dfs,ignore_index=True)
    logging.info(f"merged {ok} files → {len(all_df)} samples")
    all_df['_sort']=pd.to_datetime(all_df['feature_start_ts'])
    all_df=all_df.sort_values('_sort').drop(columns=['_sort']).reset_index(drop=True)
    n_train=int(len(all_df)*args.train_ratio)
    train=all_df[:n_train]; val=all_df[n_train:]
    tp=PROJECT_ROOT/"data"/"train.parquet"; vp=PROJECT_ROOT/"data"/"val.parquet"
    train.to_parquet(tp,index=False,engine='pyarrow'); val.to_parquet(vp,index=False,engine='pyarrow')
    mb=(tp.stat().st_size+vp.stat().st_size)/2**20
    logging.info(f"train:{len(train)} val:{len(val)} {mb:.1f}MB")
    if 'calmar' in all_df.columns:
        c=pd.to_numeric(all_df['calmar'],errors='coerce').dropna()
        logging.info(f"Calmar mean:{c.mean():.3f} median:{c.median():.3f} >0:{(c>0).mean()*100:.1f}%")
    logging.info(f"stocks covered: {all_df['stock_code'].nunique()}")

if __name__=='__main__': main()
