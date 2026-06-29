-- ============================================================
-- A股量化交易系统 TDengine 表结构 v3.0
-- 数据库: quant_dynamic
-- 执行: taos -f td_schema_v3.sql
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
-- 1. 实时行情表 (5秒级, Java 程序写入, 保留3个交易日)
--    子表: rt_{code}  例: rt_sh600036
-- ============================================================
CREATE STABLE IF NOT EXISTS rt_stock_data (
  ts              TIMESTAMP,
  price           DOUBLE,
  change_pct      DOUBLE,
  change_amt      DOUBLE,
  volume          BIGINT,
  amount          DOUBLE,
  turnover_rate   DOUBLE,
  pe_ttm          DOUBLE,
  total_mv        DOUBLE,
  amplitude       DOUBLE,
  bid1_price      DOUBLE,
  bid1_vol        BIGINT,
  ask1_price      DOUBLE,
  ask1_vol        BIGINT,
  prev_close      DOUBLE,
  today_open      DOUBLE,
  today_high      DOUBLE,
  today_low       DOUBLE
) TAGS (
  stock_code  VARCHAR(16),
  stock_name  VARCHAR(64),
  market      VARCHAR(8)
);

-- ============================================================
-- 2. 历史K线表 (1分钟级, Java 程序日终从 rt 聚合写入, 保留100个交易日)
--    子表: h_{code}  例: h_sh600036
-- ============================================================
CREATE STABLE IF NOT EXISTS hist_stock_data (
  ts      TIMESTAMP,
  open    DOUBLE,
  high    DOUBLE,
  low     DOUBLE,
  close   DOUBLE,
  volume  BIGINT,
  amount  DOUBLE
) TAGS (
  stock_code  VARCHAR(16),
  market      VARCHAR(8)
);

-- ============================================================
-- 3. 分钟K线表 (1分钟级, Python data_fetcher 写入, 非聚合原始数据)
--    子表: m_{code}  例: m_sh600036
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
-- 4. 分钟特征表 (32维, Python feature_engineering 写入)
--    子表: f_{code}  例: f_sh600036
-- ============================================================
CREATE STABLE IF NOT EXISTS features_1m (
  ts                  TIMESTAMP,
  open                DOUBLE,
  high                DOUBLE,
  low                 DOUBLE,
  close               DOUBLE,
  volume              DOUBLE,
  volume_ma5          DOUBLE,
  volume_ratio        DOUBLE,
  ma5                 DOUBLE,
  ma10                DOUBLE,
  ma20                DOUBLE,
  ma60                DOUBLE,
  rsi_6               DOUBLE,
  rsi_14              DOUBLE,
  macd                DOUBLE,
  macd_signal         DOUBLE,
  macd_hist           DOUBLE,
  atr_14              DOUBLE,
  bb_upper            DOUBLE,
  bb_lower            DOUBLE,
  bb_width            DOUBLE,
  obv                 DOUBLE,
  mfi_14              DOUBLE,
  amplitude_pct       DOUBLE,
  volume_momentum     DOUBLE,
  price_position      DOUBLE,
  sentiment           DOUBLE,
  index_change_pct    DOUBLE,
  relative_strength   DOUBLE,
  index_volume_ratio  DOUBLE,
  rank_ma5            DOUBLE,
  rank_rsi_14         DOUBLE,
  rank_volume         DOUBLE
) TAGS (
  stock_code  VARCHAR(32),
  market      VARCHAR(16)
);

-- ============================================================
-- 5. 指数分钟K线表
--    子表: i_{code}  例: i_sh000001
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
-- 6. 市场情绪表
--    子表: sent_daily / sent_hot / sent_trend
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
