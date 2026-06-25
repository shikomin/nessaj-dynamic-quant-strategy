-- ============================================================
-- A股动态参数量化交易系统 — TDengine 表结构定义 v2.2
-- ============================================================
-- 用途: 数据采集脚本重构后，旧表将被删除，执行本文件创建新表结构
-- 执行: taos -f td_schema.sql
-- ============================================================

-- 1. 创建数据库 (如需要对已有库执行，请先 DROP DATABASE IF EXISTS)
-- ============================================================
CREATE DATABASE IF NOT EXISTS quant_dynamic 
  PRECISION 'ms'
  KEEP 3650
  DURATION 10
  BUFFER 256
  PAGES 256
  PAGESIZE 4
  CACHEMODEL 'both'
  COMP 2
  WAL_LEVEL 1
  WAL_FSYNC_PERIOD 3000;

USE quant_dynamic;

-- ============================================================
-- 2. 个股 1分钟K线 超级表
-- ============================================================
-- 子表命名: m_{code}  如 m_sh600036
-- 标签: stock_code (内部代码), market (SH/SZ)
-- ============================================================
CREATE STABLE IF NOT EXISTS kline_1m (
  ts      TIMESTAMP,
  open    DOUBLE,
  high    DOUBLE,
  low     DOUBLE,
  close   DOUBLE,
  volume  BIGINT,
  amount  DOUBLE
) TAGS (
  stock_code  VARCHAR(32),
  market      VARCHAR(16)
);

-- ============================================================
-- 3. 指数 1分钟K线 超级表 (v2.2 新增)
-- ============================================================
-- 子表命名: i_{code}  如 i_sh000001 (上证指数)
-- 标签: index_code (zzshare代码), index_name (中文名称)
-- 采集目标:
--   sh000001 — 上证指数
--   sz399001 — 深证成指
--   sz399006 — 创业板指
-- ============================================================
CREATE STABLE IF NOT EXISTS index_kline_1m (
  ts      TIMESTAMP,
  open    DOUBLE,
  high    DOUBLE,
  low     DOUBLE,
  close   DOUBLE,
  volume  BIGINT,
  amount  DOUBLE
) TAGS (
  index_code  VARCHAR(32),
  index_name  VARCHAR(64)
);

-- ============================================================
-- 4. 市场情绪 超级表
-- ============================================================
-- 子表:
--   sent_daily — 每日情绪K线  (主表, 特征工程使用)
--   sent_hot   — 热门情绪
--   sent_trend — 情绪趋势
-- 标签: model (market_sentiment / market_hot_sentiment / sentiment_trend)
-- ============================================================
CREATE STABLE IF NOT EXISTS market_sentiment (
  ts      TIMESTAMP,
  open    DOUBLE,
  high    DOUBLE,
  low     DOUBLE,
  close   DOUBLE,
  volume  BIGINT,
  amount  DOUBLE
) TAGS (
  model  VARCHAR(64)
);

-- ============================================================
-- 5. 个股 1分钟特征 超级表 (v2.2, 29维)
-- ============================================================
-- 子表命名: f_{code}  如 f_sh600036
-- 标签: stock_code (内部代码), market (SH/SZ/BJ)
--
-- 特征维度 (29):
--   价格基础(4) + 成交量(3) + 趋势(4) + 动量(5) + 波动(4) +
--   量价(2) + 市场结构(3) + 情绪(1) + 大盘(3)
-- ============================================================
CREATE STABLE IF NOT EXISTS features_1m (
  ts                  TIMESTAMP,
  -- 价格基础 (4)
  open                DOUBLE,
  high                DOUBLE,
  low                 DOUBLE,
  close               DOUBLE,
  -- 成交量 (3)
  volume              DOUBLE,
  volume_ma5          DOUBLE,
  volume_ratio        DOUBLE,
  -- 趋势 (4)
  ma5                 DOUBLE,
  ma10                DOUBLE,
  ma20                DOUBLE,
  ma60                DOUBLE,
  -- 动量 (5)
  rsi_6               DOUBLE,
  rsi_14              DOUBLE,
  macd                DOUBLE,
  macd_signal         DOUBLE,
  macd_hist           DOUBLE,
  -- 波动 (4)
  atr_14              DOUBLE,
  bb_upper            DOUBLE,
  bb_lower            DOUBLE,
  bb_width            DOUBLE,
  -- 量价 (2)
  obv                 DOUBLE,
  mfi_14              DOUBLE,
  -- 市场结构 (3)
  amplitude_pct       DOUBLE,
  volume_momentum     DOUBLE,
  price_position      DOUBLE,
  -- 情绪 (1)
  sentiment           DOUBLE,
  -- v2.2 新增: 大盘特征 (3)
  index_change_pct    DOUBLE,
  relative_strength   DOUBLE,
  index_volume_ratio  DOUBLE
) TAGS (
  stock_code  VARCHAR(32),
  market      VARCHAR(16)
);
