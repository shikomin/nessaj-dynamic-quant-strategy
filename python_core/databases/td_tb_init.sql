-- 1. 创建数据库
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

-- 2. 创建日线超级表
CREATE STABLE IF NOT EXISTS quant_dynamic.kline_daily (
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

-- 3. 创建1分钟线超级表
CREATE STABLE IF NOT EXISTS quant_dynamic.kline_1m (
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