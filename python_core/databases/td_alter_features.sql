-- ============================================================
-- features_1m 超级表 v2.3: 新增 3 维截面排名特征
-- 在已有数据库上执行即可，不影响现有数据
-- ============================================================
USE quant_dynamic;

ALTER STABLE features_1m ADD COLUMN rank_ma5    DOUBLE;
ALTER STABLE features_1m ADD COLUMN rank_rsi_14 DOUBLE;
ALTER STABLE features_1m ADD COLUMN rank_volume DOUBLE;

-- 验证
DESCRIBE features_1m;
