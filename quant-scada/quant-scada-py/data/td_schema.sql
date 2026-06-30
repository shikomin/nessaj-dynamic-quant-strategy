-- ============================================================
-- TDengine 实时+历史数据库表结构
-- 数据库: quant_scada_rt (实时, 保留3天)
--         quant_scada_hist (历史, 永久保留)
-- ============================================================

-- ============================================================
-- 一、实时数据库 quant_scada_rt (数据失效3天)
-- ============================================================
CREATE DATABASE IF NOT EXISTS quant_scada_rt
  PRECISION 'ms'
  KEEP 3
  DURATION 1
  BUFFER 256
  PAGES 256
  PAGESIZE 4
  CACHEMODEL 'both'
  COMP 1
  WAL_LEVEL 1
  WAL_FSYNC_PERIOD 3000;

-- 1.1 股票实时数据超表
--     子表命名: rt_{code}  例: rt_000001_SZ
CREATE STABLE IF NOT EXISTS quant_scada_rt.stock_rt_data (
  ts              TIMESTAMP,
  price           DOUBLE,
  open            DOUBLE,
  high            DOUBLE,
  low             DOUBLE,
  pre_close       DOUBLE,
  change_pct      DOUBLE,
  volume          BIGINT,
  amount          DOUBLE,
  turnover_rate   DOUBLE,
  pe_ttm          DOUBLE,
  eps_ttm         DOUBLE,
  total_mv        DOUBLE,
  circulation_mv  DOUBLE,
  bid1_price      DOUBLE,
  bid1_vol        BIGINT,
  ask1_price      DOUBLE,
  ask1_vol        BIGINT,
  auction_vol     BIGINT,
  auction_val     DOUBLE,
  auction_px      DOUBLE,
  amplitude       DOUBLE
) TAGS (
  stock_code  VARCHAR(16),
  stock_name  VARCHAR(64),
  market      VARCHAR(8)
);

-- 1.2 指数实时数据超表
--     子表命名: idx_rt_{code}  例: idx_rt_000001
CREATE STABLE IF NOT EXISTS quant_scada_rt.index_rt_data (
  ts              TIMESTAMP,
  price           DOUBLE,
  open            DOUBLE,
  high            DOUBLE,
  low             DOUBLE,
  pre_close       DOUBLE,
  change_pct      DOUBLE,
  volume          BIGINT,
  amount          DOUBLE
) TAGS (
  index_code  VARCHAR(16),
  index_name  VARCHAR(64),
  market      VARCHAR(8)
);

-- 1.3 日内情绪超表 (5分钟级)
--     子表命名: sent_rt_{type}  例: sent_rt_overall, sent_rt_updown
CREATE STABLE IF NOT EXISTS quant_scada_rt.intraday_sentiment (
  ts                  TIMESTAMP,
  sentiment_score     DOUBLE,
  up_count            INT,
  down_count          INT,
  flat_count          INT,
  limit_up_count      INT,
  limit_down_count    INT,
  up_gt_7pct          INT,
  down_gt_7pct        INT,
  total_volume        BIGINT,
  total_amount        DOUBLE,
  volume_ratio        DOUBLE
) TAGS (
  source  VARCHAR(32)
);


-- ============================================================
-- 二、历史数据库 quant_scada_hist (数据失效0天, 即永久保留)
-- ============================================================
CREATE DATABASE IF NOT EXISTS quant_scada_hist
  PRECISION 'ms'
  KEEP 150
  DURATION 5
  BUFFER 256
  PAGES 256
  PAGESIZE 4
  CACHEMODEL 'both'
  COMP 2
  WAL_LEVEL 1
  WAL_FSYNC_PERIOD 3000;

-- 2.1 股票历史1分钟K线超表
--     子表命名: hk_{code}  例: hk_000001_SZ
CREATE STABLE IF NOT EXISTS quant_scada_hist.stock_hist_kline_1m (
  ts              TIMESTAMP,
  open            DOUBLE,
  high            DOUBLE,
  low             DOUBLE,
  close           DOUBLE,
  volume          BIGINT,
  amount          DOUBLE
) TAGS (
  stock_code  VARCHAR(16),
  market      VARCHAR(8)
);

-- 2.2 指数历史1分钟K线超表
--     子表命名: idx_hk_{code}  例: idx_hk_000001
CREATE STABLE IF NOT EXISTS quant_scada_hist.index_hist_kline_1m (
  ts              TIMESTAMP,
  open            DOUBLE,
  high            DOUBLE,
  low             DOUBLE,
  close           DOUBLE,
  volume          BIGINT,
  amount          DOUBLE
) TAGS (
  index_code  VARCHAR(16),
  index_name  VARCHAR(64)
);

-- 2.3 历史市场情绪超表
--     子表命名: sent_{type}  例: sent_overall, sent_updown, sent_hot
CREATE STABLE IF NOT EXISTS quant_scada_hist.market_hist_sentiment (
  ts                  TIMESTAMP,
  sentiment_score     DOUBLE,
  up_count            INT,
  down_count          INT,
  flat_count          INT,
  limit_up_count      INT,
  limit_down_count    INT,
  up_gt_7pct          INT,
  down_gt_7pct        INT,
  total_volume        BIGINT,
  total_amount        DOUBLE,
  volume_ratio        DOUBLE
) TAGS (
  source  VARCHAR(32)
);
