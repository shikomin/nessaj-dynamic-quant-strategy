"""
数据处理工具
=============
提供项目内共享的数据加载、清洗、转换函数。
"""
import logging
from pathlib import Path

import pandas as pd

from components.config import PROJECT_ROOT


def load_stock_list(csv_path: str) -> list[dict]:
    """
    从 CSV 文件加载股票列表。

    参数
    ----
    csv_path: CSV 文件名或相对路径 (相对于 PROJECT_ROOT)
              如 'stock_list.csv', 'data/stock_list_100.csv'

    返回
    ----
    list[dict]: 每行一个字典, 字段名来自 CSV 表头 (前后空格已去除)
                [{'代码': 'sh600036', '名称': '招商银行'}, ...]
                如果文件不存在返回空列表

    CSV 格式要求:
        - 第一行为表头, 必须有 '代码' 列 (如 sh600036)
        - 可选 '名称' 列 (如 招商银行)
        - 编码: UTF-8
    """
    path = PROJECT_ROOT / csv_path
    if not path.exists():
        logging.error(f"股票列表文件不存在: {path}")
        return []

    df = pd.read_csv(path, dtype=str)
    return [
        {k.strip(): v.strip() if isinstance(v, str) else v
         for k, v in row.items()}
        for _, row in df.iterrows()
    ]
