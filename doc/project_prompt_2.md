# A股强化学习交易系统 — 项目设计文档 v3.0

> **日期**: 2026-06-29
> **状态**: 方案设计阶段
> **核心变更**: 废弃监督学习（LSTM→策略权重），改用 PPO 强化学习（LSTM→直接交易动作）

---

## 1. 为什么废弃 v2.3

v2.3 的核心矛盾：

```
监督学习需要标签 → 标签来自回测 Calmar → Calmar 来自未来 5 天收益
                                       ↓
                    LSTM 被要求从 1 天特征预测 5 天收益 = 变相价格预测
                                       ↓
                    和"不做价格预测"的设计理念矛盾
```

| 尝试方案 | 标签来源 | 根本问题 |
|---------|---------|---------|
| v2.3 权重回归 | 未来 5 天 Calmar | ≈价格预测，信噪比极低 |
| 市态分类 | 特征窗口内规则 | 规则能算出来的东西不需要 LSTM |
| 策略激活概率 | 回测盈利标记 | 同上，标签依赖未来 |

**结论**: 只要用监督学习，"标签从哪来"就是死结。RL 不需要标签——环境直接给奖励。

---

## 2. RL 方案核心

### 2.1 一句话

LSTM 提取历史行情时序特征 → PPO 训练直接输出买卖动作 → 环境处理 T+1/税费/涨跌停 → 奖励 = 已实现收益

### 2.2 与旧方案对比

| | v2.3 监督学习 | v3.0 强化学习 |
|---|---|---|
| 策略来源 | 9 个硬编码策略公式 | LSTM 自己学买卖时机 |
| 训练信号 | 标签（Calmar 权重） | 奖励（已实现收益） |
| 学习目标 | 模仿标签分布 | 最大化累计收益 |
| 特征 | 32 维人工特征 | 15 维基础特征 + 5 维账户状态 |
| 回测引擎角色 | 标签生成器 | 交互环境 |
| 未来信息泄露 | 存在（标签用 5 天回测结果） | 无（奖励只用已发生交易） |

---

## 3. 系统架构

```
┌──────────────────────────────────────────────────────────┐
│                   训练阶段 (离线)                          │
│                                                          │
│  TDengine ──→ 行情数据 ──→ rl_env.py (Gym环境)            │
│                              │                           │
│                    state ←───┼───→ action                │
│                    reward ←──┼──→ next_state             │
│                              │                           │
│                        rl_agent.py                        │
│                        LSTM(2层,128)                      │
│                        Actor + Critic                    │
│                              │                           │
│                        rl_train.py                        │
│                        PPO + GAE                         │
│                              │                           │
│                        保存模型权重                        │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                   推理阶段 (在线)                          │
│                                                          │
│  TDengine ──→ 实时行情 ──→ rl_env.py (推理模式)           │
│                              │                           │
│                              ↓                           │
│                        加载模型权重                        │
│                        LSTM 前向推理                      │
│                              │                           │
│                        输出动作 (BUY/SELL/HOLD)            │
│                              │                           │
│                    FastAPI ──→ SpringBoot ──→ Vue        │
└──────────────────────────────────────────────────────────┘
```

---

## 4. 状态空间 (State)

### 4.1 市场特征 (15 维，进入 LSTM)

LSTM 输入为滑动窗口 `(240, 15)`，即 1 个交易日 240 根分钟 K 线：

| # | 特征 | 含义 |
|---|------|------|
| 1 | open | 开盘价 |
| 2 | high | 最高价 |
| 3 | low | 最低价 |
| 4 | close | 收盘价 |
| 5 | volume | 成交量 |
| 6 | return_1 | 1 根 K 线收益率 |
| 7 | return_5 | 5 根 K 线收益率 |
| 8 | return_20 | 20 根 K 线收益率 |
| 9 | return_60 | 60 根 K 线收益率 |
| 10 | vol_ratio | volume / volume_ma20 |
| 11 | price_vs_ma5 | close / ma5 - 1 |
| 12 | price_vs_ma20 | close / ma20 - 1 |
| 13 | atr_norm | atr_14 / close |
| 14 | bar_position | 当天第几根 (0~239) |
| 15 | is_last_hour | 尾盘 60 分钟 = 1 |

### 4.2 账户状态 (5 维，拼到 LSTM 输出后)

| # | 特征 | 含义 |
|---|------|------|
| 16 | cash_ratio | 现金 / 初始资金 |
| 17 | position_ratio | 持仓市值 / 总资产 |
| 18 | can_sell | T+1 解禁？(0/1) |
| 19 | unrealized_pnl | 未实现盈亏比例 |
| 20 | holding_bars | 已持仓 K 线数 |

> 账户特征不进入 LSTM（LSTM 只学行情时序），在 LSTM hidden state 后面 concat。

---

## 5. 动作空间 (Action)

7 个离散动作：

| ID | 动作 | 含义 |
|----|------|------|
| 0 | IDLE | 什么都不做 |
| 1 | BUY_025 | 用可用现金的 25% 买入 |
| 2 | BUY_050 | 用可用现金的 50% 买入 |
| 3 | BUY_100 | 用可用现金的 100% 买入 |
| 4 | SELL_025 | 卖出持仓的 25% |
| 5 | SELL_050 | 卖出持仓的 50% |
| 6 | SELL_100 | 卖出持仓的 100% |

**执行规则**:
- 买入按 100 股（1 手）取整，资金不够自动降级为最大可买手数
- 卖出按批次 FIFO，只卖 T+1 已解禁的批次
- 涨停不可买（动作浪费，无成本）
- 跌停不可卖（动作浪费，无成交）
- 支持部分成交（如卖 25% 但最早批次不够，拆分处理）

---

## 6. 网络结构

```
Market features (240, 15)
       │
  LSTM(128, num_layers=2, dropout=0.1)
       │
  hidden state (128)
       │
       ├── concat [account_state (5)] → combined (133)
       │         │
       │    ┌────┴────┐
       │  Actor      Critic
       │   │           │
       │  Linear      Linear
       │  (64)        (64)
       │  ReLU        ReLU
       │   │           │
       │  Linear      Linear
       │  (7)         (1)
       │   │           │
       │  softmax     state value
       │
    动作概率分布      V(s)
```

**训练 Loss**:
```
total_loss = policy_loss + c1 × value_loss - c2 × entropy_bonus

policy_loss = -min(ratio × advantage, clip(ratio, 1-ε, 1+ε) × advantage)
value_loss  = (V(s) - returns)²
entropy     = -sum(p × log(p))  鼓励探索
```

---

## 7. 环境设计 (rl_env.py)

### 7.1 核心接口

```python
class TradingEnv:
    def reset(self, stock_code, start_bar) -> np.ndarray:
        """初始化: 加载该股该窗口的行情数据，重置现金/持仓"""
        return state  # (240, 15) 特征窗口

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        """执行动作 → 推进 1 根 K 线 → 返回 (next_state, reward, done, info)"""
        ...
```

### 7.2 资金模型

```
初始资金: 50,000 元
├── 现金: 动态变化
└── 持仓批次 list[Lot], FIFO, 每批次独立 T+1

Lot = {shares: int, cost: float, entry_bar: int}
```

### 7.3 交易成本

| 项目 | 买方 | 卖方 | 说明 |
|------|------|------|------|
| 佣金 | 0.01% | 0.01% | 万一 |
| 印花税 | — | 0.1% | 卖出单向，千一 |
| 滑点 | 0.05% | 0.05% | 固定滑点 |

### 7.4 涨跌停规则

| 板块 | 涨跌停幅度 |
|------|----------|
| 主板 (sh60/sz00) | ±10% |
| 创业板 (sz30) | ±20% |
| 科创板 (sh688) | ±20% |
| 北交所 (bj) | ±30% |
| ST 股票 | ±5% |

一字板检测：`open == high == low == close ≥ limit_up` → 不可买入。

### 7.5 Episode 设计

```
|── 预热区 (240 bar) ──|─────── Episode (1200 bar = 5 交易日) ────────|
    ↑ LSTM 输入窗口        ↑ 每 bar 做一次决策
                            ↑ T+1: 今日买入 → 次日才能卖
```

- 窗口滑动步长 = 240 bar (1 天)
- 每只股约 245 个 episode
- 300 只股 × 245 ≈ 73,500 episode
- Episode 末强制平仓（市价卖出所有持仓），计算最终收益

### 7.6 奖励信号

```
动作          即时奖励
──────────────────────────────
BUY          -(佣金 + 滑点)        负奖励抑制频繁交易
SELL         已实现盈亏 - 税费      兑现的收益/亏损
IDLE         0
──────────────────────────────
Episode 末   持仓按市价强制平仓收益  防止学成"永远不卖"
```

---

## 8. PPO 训练流程 (rl_train.py)

### 8.1 训练循环

```python
for epoch in range(N_EPOCHS):
    # 1. 采样 (10 个环境并行)
    buffer = []
    for env in parallel_envs:
        state = env.reset()
        for t in range(ROLLOUT_STEPS):
            action, log_prob, value = agent.act(state)
            next_state, reward, done = env.step(action)
            buffer.append(state, action, reward, log_prob, value, done)
            state = next_state

    # 2. 计算优势 (GAE)
    advantages = compute_gae(
        rewards, values, dones,
        gamma=0.99, lambda_=0.95
    )

    # 3. PPO 更新
    for _ in range(PPO_EPOCHS):
        for batch in buffer.shuffle().batch(256):
            new_log_prob, new_value, entropy = agent.evaluate(batch)
            ratio = exp(new_log_prob - batch.log_prob)
            surr1 = ratio * batch.advantage
            surr2 = clip(ratio, 0.8, 1.2) * batch.advantage
            policy_loss = -min(surr1, surr2).mean()
            value_loss = (new_value - batch.returns).pow(2).mean()
            loss = policy_loss + 0.5 * value_loss - 0.01 * entropy
            optimizer.zero_grad(); loss.backward(); optimizer.step()

    # 4. 验证
    val_score = evaluate(agent, val_envs)
    if val_score > best_score:
        save_checkpoint(agent, epoch)
```

### 8.2 超参数

| 参数 | 值 | 说明 |
|------|-----|------|
| gamma | 0.99 | 折扣因子 |
| lambda (GAE) | 0.95 | 偏差-方差权衡 |
| clip_epsilon | 0.2 | PPO clip 范围 |
| learning_rate | 3e-4 | Adam |
| rollout_steps | 4096 | 每轮采样步数 |
| batch_size | 256 | 小批量 |
| ppo_epochs | 4 | 每轮更新次数 |
| entropy_coef | 0.01 | 探索奖励 |
| value_coef | 0.5 | 价值函数权重 |
| max_grad_norm | 0.5 | 梯度裁剪 |
| parallel_envs | 10 | 并行环境数 |

### 8.3 验证集划分

| 集合 | 股票 | 时间 | 用途 |
|------|------|------|------|
| 训练 | 210 只 | 前 200 天 | 策略学习 |
| 验证 | 210 只 | 后 50 天 | 超参调优 + 早停 |
| 测试 | 90 只 | 全部 | 最终泛化评估 |

---

## 9. 新旧代码衔接

| 操作 | 文件 | 说明 |
|------|------|------|
| **新建** | `python_core/src/rl_env.py` | Gym 环境: T+1/税费/涨跌停/批次管理 |
| **新建** | `python_core/src/rl_agent.py` | LSTM + Actor-Critic 网络 |
| **新建** | `python_core/src/rl_train.py` | PPO + GAE 训练循环 |
| **新建** | `python_core/src/rl_feature.py` | 轻量特征计算 (15 维) |
| **提取** | 从 `backtest_engine.py` → `rl_env.py` | T+1 管理 / 涨跌停检测 / 税费逻辑 |
| **归档** | `sample_generator.py` | 不再需要 |
| **归档** | `merge_samples.py` | 不再需要 |
| **归档** | `cross_sectional.py` | 不再需要 |
| **归档** | `feature_engineering.py` | 被 `rl_feature.py` 替代 |
| **保留** | `data_fetcher.py` / `td_connector.py` | 数据管线不变 |

---

## 10. 实施计划

| 阶段 | 任务 | 预估 |
|------|------|------|
| **当前** | 前后端实盘交易 + 历史浏览系统 | 2-3 周 |
| Phase 1 | `rl_env.py` — 环境封装，确保回测引擎逻辑正确 | 2-3 天 |
| Phase 2 | `rl_agent.py` — 网络定义 + 前向推理测试 | 1-2 天 |
| Phase 3 | `rl_train.py` — PPO 训练循环 + GAE | 3-4 天 |
| Phase 4 | 小规模训练验证（1 只股，检查收敛） | 2-3 天 |
| Phase 5 | 全量训练（210 只股）+ 调参 | 1-2 周 |
| Phase 6 | 测试集评估 + 模型分析 | 2-3 天 |
| Phase 7 | FastAPI 推理服务 + 前后端对接 | 3-5 天 |

---

## 11. 风险与注意事项

1. **PPO 收敛不稳定**: 离散动作 + 稀疏奖励的组合可能需要加 shaped reward（如持仓期间净值变化）。先用 1 只股快速迭代验证算法能收敛。

2. **LSTM 梯度消失**: 240 步长对 LSTM 来说偏长。如果训练困难，考虑：
   - 降采样（每 5 根 K 线合并）→ 48 步
   - 或改用 Transformer（self-attention 无长距离衰减）

3. **探索不足**: 初始策略可能永远不买/永远不卖。加大 entropy bonus 或用 epsilon-greedy 热启动。

4. **过拟合**: 300 只股 250 天可能不够。测试集必须是未见过的股票，确保学到的是通用规律而非单股记忆。

5. **算力需求**: PPO 训练 70,000+ episode、每 episode 1200 步，需要 GPU（建议至少 RTX 3060 12GB）。纯 CPU 训练会非常慢。
