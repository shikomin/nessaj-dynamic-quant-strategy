# Python 编码规范

> **项目**: A股动态参数量化交易系统
> **版本**: v2.2
> **适用范围**: python_core/ 下所有 .py 文件

---

## 一、命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 模块/文件名 | snake_case | `data_fetcher.py`, `proxy_utils.py` |
| 类名 | PascalCase | `TdConnector`, `ZZShareFetcher` |
| 函数/方法 | snake_case | `load_stock_list()`, `compute_features()` |
| 变量 | snake_case | `stock_code`, `total_written` |
| 常量 | UPPER_SNAKE_CASE | `COMMISSION`, `FEATURE_WINDOW` |
| 私有函数 | 前缀 `_` | `_calc_atr()`, `_gen_buy_signal()` |
| 布尔变量 | `is_` / `has_` 前缀 | `is_limit_up`, `has_gap` |

## 二、文件结构

```python
#!/usr/bin/env python3
"""
模块文档字符串：简要说明模块用途、输入输出、用法示例。
"""
# --- 标准库 ---
import sys
import time
from pathlib import Path

# --- 第三方库 ---
import numpy as np
import pandas as pd

# --- 项目路径初始化 ---
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# --- 项目组件 ---
from components.config import load_config, PROJECT_ROOT
from components.logger import setup_logging

# --- 项目工具 ---
from utils.proxy_utils import disable_system_proxy

# ============================================================
# 常量定义
# ============================================================

# ============================================================
# 类定义
# ============================================================

# ============================================================
# 函数定义
# ============================================================

# ============================================================
# 主入口
# ============================================================
```

## 三、注释规范

1. **所有函数必须有文档字符串**，说明：作用、参数、返回值
2. **复杂逻辑用 `──` 分隔线分块**，每块有简短说明
3. **关键设计决策用注释标注 `★` 或 `⚠`** 标记需要特别注意的地方
4. **注释用中文**，技术术语（API、DataFrame、LSTM）保留英文
5. **不写无意义注释**（如 `i += 1  # i加1`）

```python
def _simulate(open_, high, low, close, atr, ts, ...):
    """
    逐根 K 线模拟单资金池 + 多批次交易。

    参数
    ----
    open_/high/low/close : 价格数组
    atr                  : ATR 数组, 用于追踪止损
    ...

    返回
    ----
    list[dict]: 交易记录列表
    """
    # ── 初始化 ──
    lots = []       # 持仓批次列表
    cash = 50000.0  # 可用现金

    for i in range(len(close)):
        # ── 检查风控信号 (最高优先级) ──
        ...
```

## 四、硬编码规则

**禁止以下硬编码**：
- 数据库连接地址/密码 → 使用 `config.yaml`
- API Token/Key → 使用 `config.yaml` 或环境变量
- 股票代码列表 → 使用 CSV 文件 + `load_stock_list()`
- 文件路径 → 基于 `PROJECT_ROOT` 拼接
- 数字常量（佣金率、滑点、窗口大小）→ 模块顶部常量定义

**允许以下硬编码**：
- 调试用的临时值（需加 `# TODO: 从配置读取` 注释）
- Python 标准库默认参数
- 数学常量（`1e-9` 用于除零保护）

```python
# 正确
from components.config import load_config
config = load_config()
host = config['tdengine']['host']

# 正确
COMMISSION = 0.001  # 万一佣金，模块顶部常量

# 错误
td = TdConnector("124.221.130.19", 6041, "root", "Nessaj@111")
```

## 五、错误处理

1. **关键操作必须 try/except**：数据库连接、API 调用、文件 I/O
2. **异常必须记录日志**：`logging.error(f"xxx 失败: {e}")`，不要 `pass`
3. **单点失败不中断全局**：一只股票拉取失败 → 记录后 continue
4. **finally 释放资源**：数据库连接、文件句柄

```python
try:
    td.connect()
    result = td.query(sql)
except Exception as e:
    logging.error(f"查询失败: {e}")
    return []
finally:
    td.close()
```

## 六、日志规范

| 级别 | 用途 | 示例 |
|------|------|------|
| INFO | 流程节点、统计数据 | `"已连接 TDengine"`, `"写入 500 条"` |
| WARNING | 可恢复的问题 | `"股票无数据，跳过"`, `"API 返回空"` |
| ERROR | 不可恢复的失败 | `"数据库连接失败"` |
| DEBUG | 调试详细信息 | `"批次 3/10: 2400 条"` |

- 日志消息格式：`f"  {变量}: {描述}"` （两个空格缩进表示子步骤）
- 不记录密码/Token

## 七、代码复用

- 重复出现 ≥3 次的函数 → 提取到 `utils/` 或 `components/`
- 不应在业务脚本中出现基础设施代码（连接管理、日志配置）
- 不应在业务脚本中出现重复的工具函数

## 八、性能注意事项

1. 大数据量用 numpy 数组 + 向量化，避免 Python 循环逐行处理
2. DataFrame 操作避免链式索引（`df[a][b]` → `df.loc[a, b]`）
3. 批量写入 TDengine（每批 ≥300 条），避免逐行 INSERT
4. 长时间循环加 `time.time()` 打点记录进度
