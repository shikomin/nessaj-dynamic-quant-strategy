select count(1) from quant_dynamic.features_1m;

SELECT CONCAT('DELETE FROM quant_dynamic.', TBNAME, ';') AS delete_statement
FROM quant_dynamic.features_1m
GROUP BY TBNAME
HAVING COUNT(*) != 0;
再用shell脚本执行这个些查询出的语句删除。
DELETE FROM quant_dynamic.f_sz300798;
DELETE FROM quant_dynamic.f_sz300665;
DELETE FROM quant_dynamic.f_sz300061;
DELETE FROM quant_dynamic.f_sz300139;
DELETE FROM quant_dynamic.f_sz300774;
DELETE FROM quant_dynamic.f_sz300778;
DELETE FROM quant_dynamic.f_sz301230;
DELETE FROM quant_dynamic.f_sz300912;
DELETE FROM quant_dynamic.f_sz301001;
DELETE FROM quant_dynamic.f_sz300241;
DELETE FROM quant_dynamic.f_sz301596;
DELETE FROM quant_dynamic.f_sz300852;
DELETE FROM quant_dynamic.f_sz301584;
DELETE FROM quant_dynamic.f_sz301056;
DELETE FROM quant_dynamic.f_sz301276;
DELETE FROM quant_dynamic.f_sz301251;
DELETE FROM quant_dynamic.f_sz300592;


CREATE STABLE IF NOT EXISTS quant_dynamic.features_1m (
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
  -- 大盘特征 (3)
  index_change_pct    DOUBLE,
  relative_strength   DOUBLE,
  index_volume_ratio  DOUBLE,
  -- v2.3 新增: 截面排名特征 (3)
  rank_ma5            DOUBLE,
  rank_rsi_14         DOUBLE,
  rank_volume         DOUBLE
) TAGS (
  stock_code  VARCHAR(32),
  market      VARCHAR(16)
);
