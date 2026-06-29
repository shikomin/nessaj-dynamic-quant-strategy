import pymysql
import logging
from config.settings import Config

logger = logging.getLogger(__name__)


def load_stocks_from_mysql():
    """从 MySQL stock_base_info 加载全部A股代码 (code.sub_market 格式)"""
    conn = pymysql.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DATABASE,
        charset="utf8mb4"
    )
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT code, sub_market FROM stock_base_info "
            "WHERE market = 'A' AND code IS NOT NULL AND sub_market IS NOT NULL"
        )
        stocks = []
        for code, sub_market in cursor.fetchall():
            ts_code = f"{code}.{sub_market}"
            stocks.append(ts_code)
        cursor.close()
        logger.info("Loaded %d stocks from MySQL stock_base_info", len(stocks))
        return stocks
    finally:
        conn.close()
