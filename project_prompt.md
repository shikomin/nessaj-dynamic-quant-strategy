# A股动态参数量化交易系统 - 项目开发文档 (Project Prompt)

## 1. 项目概述 (Project Overview)

### 1.1 核心理念
本项目旨在构建一个 **“动态参数量化交易系统”** 。其核心思想是：**固定交易策略的逻辑结构，但利用神经网络模型，根据当前市场行情动态调整策略的参数**。

系统不预测股价涨跌，而是预测“在当前市场状态下，什么参数组合最有效”。通过这种方式，让策略具备自适应能力，避免传统量化策略在参数固化后失效的问题。

### 1.2 技术架构总览
- **编程语言**：Python (核心算法/回测/模型) + Java (后端服务) + TypeScript (前端界面)
- **数据层**：TDengine (时序数据库) + MySQL (关系型数据库)
- **计算层**：PyTorch (LSTM神经网络) + VectorBT / Pandas (向量化回测)
- **应用层**：SpringBoot (业务API) + FastAPI (模型推理网关)
- **前端层**：Vue3 + Vite + TypeScript + Electron (桌面客户端)

### 1.3 数据流向
1.  **数据采集**：AkShare -> TDengine (存储原始K线)。
2.  **样本生成**：TDengine -> VectorBT (回测) -> Parquet (训练集)。
3.  **模型训练**：Parquet -> PyTorch (LSTM训练) -> Model PTH (权重文件)。
4.  **实盘推理**：实时行情 -> FastAPI (加载模型) -> 输出动态参数。
5.  **交易执行**：动态参数 -> 交易接口 (QMT/PTrade) -> 执行买卖。


## 2. 模块详细设计 (Module Specifications)

### 2.1 数据采集与存储模块 (Data Module)
**目标**：获取高质量的行情数据并实现高效存储。

- **数据源**：使用 **AkShare** 库 (免费) 或 Tushare Pro (付费/积分)。
- **标的池**：沪深300成分股 (约300只，初期建议筛选流动性前100只)。
- **时间粒度**：
    - **训练用**：日线数据 (复权因子处理)。
    - **推理用**：1分钟/5分钟 K线 (用于捕捉近期微观结构)。
- **存储设计 (TDengine)**：
    - 创建超级表 `kline_daily`，Tags: `stock_code`，Columns: `ts`, `open`, `high`, `low`, `close`, `volume`, `amount`。
    - 创建普通表 `stock_strategy_params` 用于存储历史模型输出的参数及对应收益，用于后续监控。

### 2.2 核心策略逻辑 (Fixed Super-Strategy)
**目标**：定义一个逻辑固定、参数可变的策略模板。

- **策略原型**：**双均线通道突破 + 动态止损** (作为MVP)。
- **逻辑描述**：
    1.  当 `短期均线` (参数1) 上穿 `长期均线` (参数2) 时开仓。
    2.  持仓过程中，动态止盈止损：价格跌破开仓价减去 `止损百分比` (参数3) 时平仓。
    3.  当 `短期均线` 下穿 `长期均线` 时平仓。
- **参数向量定义**：`[short_window, long_window, stop_loss_percent]`。
    - 范围限制：short_window (2-20)，long_window (20-120)，stop_loss (0.01-0.10)。

### 2.3 训练集样本生成模块 (Data Labeling & Generation)
**目标**：利用历史数据回测，生成 `(行情特征, 最优参数)` 的配对数据。

- **时间窗口设计** (重点：防未来函数)：
    - **特征窗口 (Feature Window)**：`[T-3天, T时刻]` 的历史行情 (作为模型输入)。
    - **回测窗口 (Backtest Window)**：`[T时刻, T+20天]` (作为策略执行期)。
    - **关键约束**：两个窗口**不能重叠**，且采样步长必须大于窗口总长度，防止数据交叉泄露。
- **最优参数寻优 (Label Generation)**：
    - 针对每个回测窗口，使用 **Optuna** 框架在参数空间内进行随机搜索。
    - **评价指标**：寻找该时间段内 **Calmar比率** (年化收益/最大回撤) 最高的参数组合，作为该样本的标签 (Label)。
- **输出格式**：保存为 **Parquet** 文件，包含字段：`features` (数组), `labels` (数组), `sharpe_ratio` (浮点, 用于加权损失)。

### 2.4 神经网络模型设计 (Model Architecture)
**目标**：构建一个能够从时序数据中提取特征并回归出策略参数的模型。

- **模型类型**：**LSTM + 全连接 (MLP)** 回归模型。
- **输入层**：
    - Shape: `(Batch_Size, Sequence_Length=3天, Feature_Num=6)`。
    - 特征维度：`Open`, `High`, `Low`, `Close`, `Volume`, `Amount` (需归一化)。
- **隐藏层**：
    1.  LSTM 层: `hidden_size=128`, `num_layers=2`, `dropout=0.3`。
    2.  全连接层 1: `Linear(128 -> 64)`, 激活函数 `ReLU`。
    3.  Dropout: `p=0.3`。
- **输出层**：
    - `Linear(64 -> 3)` (对应3个策略参数)。
    - **激活函数约束**：使用 `Sigmoid` 将输出映射到参数定义域内 (例如 `output * (max-min) + min`)。
- **损失函数**：**加权均方误差 (Weighted MSE)**。
    - 只对收益为正的样本进行强学习，亏损样本权重设为 `0.1` 或丢弃。
    - 公式：`Loss = mean(weight * (pred - label)^2)`。
- **优化器**：`Adam`, `learning_rate = 0.001`。

### 2.5 后端服务与模型部署 (Backend & Serving)
**目标**：将训练好的模型部署为微服务，供Java后端调用。

- **Java SpringBoot**：
    - 负责业务逻辑、用户鉴权、策略管理。
    - 提供WebSocket接口，向前端推送实时计算结果。
- **Python FastAPI** (模型网关)：
    - 加载 `model.pth` 权重文件。
    - 接收来自Java后端的请求 (输入: 股票代码 + 时间戳)。
    - 从TDengine读取该股票近3日数据，经预处理后输入模型推理。
    - 返回推理结果 (策略参数向量) 给Java后端。

### 2.6 前端监控与可视化 (Frontend Dashboard)
**目标**：提供一个可视化的桌面工具，监控策略运行状态。

- **技术栈**：Vue3 + Vite + TypeScript + Electron + ECharts。
- **核心界面**：
    1.  **K线图**：显示标的股票K线，叠加显示模型实时推荐的均线参数 (动态均线)。
    2.  **参数面板**：显示当前模型推理出的 `[Short, Long, Stop]` 数值。
    3.  **回测进度条**：当进行回测样本生成时，显示进度百分比。
    4.  **历史表现**：展示模型参数的历史变化曲线及对应的收益回撤图。


## 3. 实施步骤 (Action Plan for AI Coding Assistant)

请按照以下步骤协助生成代码。**请优先完成步骤1-3，确保数据流打通后再进行模型训练。**

### 第一阶段：基础设施搭建 (Day 1-2)
- [ ] **任务 1.1**：编写 Python 脚本 `data_fetcher.py`。
    - 使用 `akshare` 获取沪深300列表。
    - 循环下载100只股票近2年的日线复权数据。
    - 建立TDengine连接，将数据批量写入超级表。
- [ ] **任务 1.2**：配置 `application.yml` (SpringBoot) 连接 MySQL 和 TDengine。
- [ ] **任务 1.3**：初始化 Vue3 + Electron 项目，配置 ECharts 组件。

### 第二阶段：样本生产流水线 (Day 3-5)
- [ ] **任务 2.1**：编写 `backtest_engine.py`。
    - 定义核心策略类 (继承自 `vectorbt` 或使用 Pandas 向量化计算)。
    - 实现函数：`evaluate_params(stock_code, start_date, params)` 返回该时间段的收益率。
- [ ] **任务 2.2**：编写 `sample_generator.py`。
    - 使用 `Optuna` 对每个时间窗口进行参数寻优。
    - 保存特征和标签到 `data/train.parquet`。
    - **注意**：实现滑动窗口时，步长必须大于窗口长度 (防止重叠)。

### 第三阶段：模型训练与验证 (Day 6-9)
- [ ] **任务 3.1**：编写 `dataset.py`。
    - 实现 `torch.utils.data.IterableDataset`，读取 Parquet 文件。
    - 实现数据标准化 (StandardScaler) 并保存 scaler.pkl。
- [ ] **任务 3.2**：编写 `model.py`。
    - 构建 LSTM + MLP 网络结构。
    - 实现加权损失函数 (Weighted MSELoss)。
- [ ] **任务 3.3**：编写 `train.py`。
    - 实现训练循环，使用验证集监控过拟合。
    - 保存最佳模型权重到 `models/best_model.pth`。
    - 绘制训练损失曲线并保存为 `loss_plot.png`。

### 第四阶段：服务集成与实盘模拟 (Day 10-12)
- [ ] **任务 4.1**：编写 `fastapi_app.py`。
    - 加载模型和标准化器。
    - 定义接口 `POST /predict`，接收 `stock_code`，查询TDengine最近3天数据，返回JSON格式的参数。
- [ ] **任务 4.2**：SpringBoot 集成。
    - 编写 `RestTemplate` 调用 Python 服务。
    - 编写 WebSocket 处理器，将结果推送到前端。
- [ ] **任务 4.3**：前端联调。
    - 接收WebSocket数据，更新ECharts图表中的均线数值。


## 4. 技术栈详细清单 (Tech Stack Details)

| 层级 | 技术/框架 | 版本/备注 |
| :--- | :--- | :--- |
| **数据获取** | AkShare | `pip install akshare` |
| **数据库-时序** | TDengine | v3.0+ (使用 RESTful 接口或 Python Connector) |
| **数据库-关系** | MySQL | 8.0+ (使用 MyBatis-Plus) |
| **回测计算** | VectorBT | `pip install vectorbt` (向量化计算，速度极快) |
| **参数寻优** | Optuna | `pip install optuna` (用于生成训练标签) |
| **机器学习** | PyTorch | 2.0+ (CUDA 可选，CPU亦可) |
| **模型服务** | FastAPI | 异步高性能，自动生成Swagger文档 |
| **后端框架** | SpringBoot | 2.7+ (Java 8 或 17) |
| **前端框架** | Vue3 + Vite | 组合式API (Composition API) |
| **桌面壳子** | Electron | 用于打包桌面应用 |
| **图表引擎** | ECharts | 用于绘制K线、收益曲线 |


## 5. 关键注意事项与风险提示

1.  **未来函数 (Look-ahead Bias)**：
    - **禁止**在生成训练标签时使用窗口之外的数据。
    - 切分训练集/验证集时，必须按**时间顺序**切分 (例如 2018-2021训练，2022验证)。**严禁**随机打乱时间序列。
2.  **过拟合防范**：
    - 监控验证集Loss，若验证集Loss上升而训练集Loss下降，立即早停 (Early Stopping)。
    - 在LSTM层后添加Dropout。
3.  **交易摩擦**：
    - 回测和实盘计算中，必须考虑 **千分之1.5** 的手续费和滑点，否则回测收益会严重失真。
4.  **系统解耦**：
    - Java后端和Python模型服务不要强依赖。模型服务崩溃时，Java后端应能返回默认安全参数 (如保守的均线组合)，保证系统可用性。


## 6. 项目文件结构建议 (Project Tree)

```text
nessaj-dynamic-quant-strategy/
├── backend_java/                # SpringBoot 后端
│   ├── src/main/java/
│   └── pom.xml
├── python_core/                 # Python 核心计算模块
│   ├── data/                    # 数据存储
│   │   ├── raw/                 # 原始CSV (备份)
│   │   └── train.parquet        # 训练集
│   ├── models/                  # 模型存储
│   │   ├── best_model.pth
│   │   └── scaler.pkl
│   ├── data_fetcher.py          # 数据采集
│   ├── sample_generator.py      # 回测生成样本 (核心)
│   ├── model.py                 # 网络定义
│   ├── train.py                 # 训练脚本
│   └── fastapi_app.py           # 推理服务
├── frontend_vue/                # Vue3 前端
│   ├── src/
│   │   ├── components/          # K线图、参数面板
│   │   └── views/
│   └── package.json
└── README.md