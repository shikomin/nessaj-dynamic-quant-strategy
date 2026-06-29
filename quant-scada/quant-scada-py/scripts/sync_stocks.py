"""
同步全市场A股股票列表到 MySQL stock_base_info 表
数据来源: zzshare stock_basic() (一次拉取SSE+SZSE+BSE全量)
用法: python scripts/sync_stocks.py
"""
import os
import sys
import logging
import yaml
import pymysql
from pathlib import Path
from dotenv import load_dotenv

# 加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载 .env (MySQL 配置)
load_dotenv(Path(__file__).parent.parent / "config" / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ---- 配置 ----
MYSQL_HOST = os.getenv("MYSQL_HOST", "124.221.130.19")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "Password@111")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "quant_scada")

# zzshare 配置路径 (相对于 quant-scada-py 目录)
ZZSHARE_CONFIG_PATH = Path(__file__).parent.parent.parent / ".." / "python_core" / "config" / "config.yaml"


def load_zzshare_token():
    """从 python_core/config/config.yaml 读取 zzshare token"""
    config_path = ZZSHARE_CONFIG_PATH.resolve()
    if not config_path.exists():
        logger.warning("zzshare config not found: %s, using anonymous", config_path)
        return None
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    token = cfg.get("zzshare", {}).get("token", "")
    if token:
        logger.info("Loaded zzshare token from %s", config_path)
        return token
    return None


def get_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4"
    )


def sync():
    from zzshare.client import DataApi

    token = load_zzshare_token()
    api = DataApi(token=token) if token else DataApi()

    logger.info("Fetching all A-share stocks via zzshare stock_basic() ...")
    df = api.stock_basic(list_status="L")

    if df is None or df.empty:
        logger.error("No stocks returned")
        return

    logger.info("Got %d stocks", len(df))

    conn = get_conn()
    cursor = conn.cursor()
    count = 0

    for _, row in df.iterrows():
        ts_code = row.get("ts_code", "")
        code = row.get("symbol", "")
        name = row.get("name", "")
        exchange = row.get("exchange", "")
        sector_raw = row.get("market", "")

        if not code or not name:
            continue

        # 子市场: ts_code 后缀
        sub_market = ts_code.split(".")[-1] if "." in ts_code else ""

        # 大市场: A股
        market = "A"

        # 板块
        if sector_raw:
            sector = sector_raw
        elif exchange == "BSE":
            sector = "北交所"
        elif exchange == "SSE" or sub_market == "SH":
            sector = "主板"
        elif exchange == "SZSE" or sub_market == "SZ":
            sector = "主板"
        else:
            sector = "其他"

        try:
            cursor.execute(
                "INSERT INTO stock_base_info (code, market, sub_market, sector, name) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE name = VALUES(name), sector = VALUES(sector)",
                (code, market, sub_market, sector, name)
            )
            count += 1
        except Exception as e:
            logger.warning("insert failed %s: %s", code, e)

    conn.commit()
    cursor.close()
    conn.close()
    logger.info("Done. Written: %d stocks", count)


if __name__ == "__main__":
    logger.info("Stock sync start")
    sync()
