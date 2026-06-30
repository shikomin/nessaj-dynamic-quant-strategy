"""
导入通达信 .lc1 分钟K线到 TDengine stock_hist_kline_1m
数据来源: vipdoc/{sh,sz,bj}/minline/*.lc1
用法: python scripts/import_hist_kline.py
"""
import os
import sys
import struct
import signal
import pymysql
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

# 加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / "config" / ".env")

from config.settings import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ── 常量 ──
RECORD_SIZE = 32
STRUCT_FORMAT = '<HHfffffII'  # date, minute, open, high, low, close, amount, vol, reserved
BATCH_SIZE = 500              # 每批 INSERT 行数 (TDengine 单 SQL 不宜过大)
WORKERS = 6                   # 并发文件处理线程数
HIST_DB = Config.HIST_DATABASE

VIPDOC_ROOT = Path(__file__).parent / "vipdoc"
MARKET_DIRS = {
    "sh": ("SH", VIPDOC_ROOT / "sh" / "minline"),
    "sz": ("SZ", VIPDOC_ROOT / "sz" / "minline"),
    "bj": ("BJ", VIPDOC_ROOT / "bj" / "minline"),
}


def build_dsn():
    return Config.TD_DSN


def parse_file(filepath, stock_code, market):
    """
    解析单个 .lc1 文件，返回记录列表。
    每条记录: (ts_str, open, high, low, close, volume, amount)
    """
    records = []
    with open(filepath, 'rb') as f:
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        f.seek(0)
        record_count = file_size // RECORD_SIZE

        for _ in range(record_count):
            data = f.read(RECORD_SIZE)
            if len(data) < RECORD_SIZE:
                break
            try:
                date_num, minute_num, open_p, high_p, low_p, close_p, amount, volume, _reserved = \
                    struct.unpack(STRUCT_FORMAT, data)

                year = date_num // 2048 + 2004
                month = (date_num % 2048) // 100
                day = date_num % 2048 % 100
                hours = minute_num // 60
                minutes = minute_num % 60

                ts_str = f"{year:04d}-{month:02d}-{day:02d} {hours:02d}:{minutes:02d}:00.000"
                records.append((ts_str, open_p, high_p, low_p, close_p, int(volume), amount))
            except Exception:
                continue

    return stock_code, market, records


def build_insert_sql(stock_code, market, batch):
    """构建批量 INSERT SQL (股票)"""
    values_parts = []
    for row in batch:
        ts, open_p, high_p, low_p, close_p, vol, amt = row
        values_parts.append(
            f"('{ts}', {open_p}, {high_p}, {low_p}, {close_p}, {vol}, {amt})"
        )
    values_str = " ".join(values_parts)
    return (
        f"INSERT INTO {HIST_DB}.hk_{stock_code} "
        f"USING {HIST_DB}.stock_hist_kline_1m "
        f"TAGS ('{stock_code}', '{market}') "
        f"VALUES {values_str}"
    )


def build_index_insert_sql(index_code, index_name, batch):
    """构建批量 INSERT SQL (指数)"""
    values_parts = []
    for row in batch:
        ts, open_p, high_p, low_p, close_p, vol, amt = row
        values_parts.append(
            f"('{ts}', {open_p}, {high_p}, {low_p}, {close_p}, {vol}, {amt})"
        )
    values_str = " ".join(values_parts)
    return (
        f"INSERT INTO {HIST_DB}.idx_hk_{index_code} "
        f"USING {HIST_DB}.index_hist_kline_1m "
        f"TAGS ('{index_code}', '{index_name}') "
        f"VALUES {values_str}"
    )


def import_stock(filepath, stock_code, market):
    """导入单只股票的所有1分钟K线到 TDengine"""
    stock_code_str, market_str, records = parse_file(filepath, stock_code, market)
    if not records:
        return 0

    total = 0
    import taosws
    dsn = build_dsn()
    conn = taosws.connect(dsn)

    try:
        # 先删旧数据
        try:
            conn.execute(f"DELETE FROM {HIST_DB}.hk_{stock_code_str}")
        except Exception:
            pass

        # 批量插入
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            sql = build_insert_sql(stock_code_str, market_str, batch)
            try:
                conn.execute(sql)
                total += len(batch)
            except Exception as e:
                logger.warning("%s batch %d failed: %s", stock_code_str, i // BATCH_SIZE, e)
    finally:
        conn.close()

    return total


def import_index(filepath, index_code, index_name):
    """导入单只指数的所有1分钟K线到 TDengine"""
    _, _, records = parse_file(filepath, index_code, '')
    if not records:
        return 0, index_code

    total = 0
    import taosws
    dsn = build_dsn()
    conn = taosws.connect(dsn)

    try:
        try:
            conn.execute(f"DELETE FROM {HIST_DB}.idx_hk_{index_code}")
        except Exception:
            pass

        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            sql = build_index_insert_sql(index_code, index_name, batch)
            try:
                conn.execute(sql)
                total += len(batch)
            except Exception as e:
                logger.warning("index %s batch %d failed: %s", index_code, i // BATCH_SIZE, e)
    finally:
        conn.close()

    return total, index_code


# ── 指数名称映射 ──
INDEX_NAME_MAP = {
    "000001": "上证指数",
    "000016": "上证50",
    "000300": "沪深300",
    "000688": "科创50",
    "000852": "中证1000",
    "000905": "中证500",
    "399001": "深证成指",
    "399006": "创业板指",
    "399106": "深证综指",
    "399300": "沪深300",
    "899050": "北证全指",
}


def index_name(prefix, code):
    return INDEX_NAME_MAP.get(code, f"{prefix.upper()}{code}")


def load_stock_map():
    """从 MySQL stock_base_info 加载股票列表，返回 set: {'sh:600001', ...}"""
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
            "SELECT LOWER(sub_market), code FROM stock_base_info WHERE market = 'A'"
        )
        stock_map = set()
        for sub_market, code in cursor.fetchall():
            stock_map.add(f"{sub_market}:{code}")
        cursor.close()
        logger.info("Loaded %d stocks from MySQL", len(stock_map))
        return stock_map
    finally:
        conn.close()


def main():
    logger.info("Starting 1min K-line import...")

    # 加载 MySQL 股票列表
    stock_map = load_stock_map()

    # 收集并分类: 股票(MySQL中) / 指数(在INDEX_NAME_MAP中) / 跳过
    stock_files = []
    index_files = []
    skipped = 0
    for prefix, (market, dirpath) in MARKET_DIRS.items():
        if not dirpath.exists():
            logger.warning("Directory not found: %s", dirpath)
            continue
        for fname in dirpath.iterdir():
            if not fname.name.endswith('.lc1'):
                continue
            code = fname.stem[len(prefix):]
            key = f"{prefix}:{code}"
            if key in stock_map:
                stock_files.append((fname, code, market))
            elif code in INDEX_NAME_MAP:
                index_files.append((fname, code, index_name(prefix, code)))
            else:
                skipped += 1

    logger.info("Files: %d stocks, %d indices, %d skipped",
                len(stock_files), len(index_files), skipped)

    total_records = 0
    completed = 0
    total_files = len(stock_files) + len(index_files)

    pool = ThreadPoolExecutor(max_workers=WORKERS)
    shutdown_flag = False

    def _on_interrupt(signum, frame):
        nonlocal shutdown_flag
        if not shutdown_flag:
            shutdown_flag = True
            logger.warning("Interrupted, shutting down (waiting for running threads)...")
            pool.shutdown(wait=True, cancel_futures=True)

    signal.signal(signal.SIGINT, _on_interrupt)
    signal.signal(signal.SIGTERM, _on_interrupt)

    try:
        futures = {}
        for fp, code, mkt in stock_files:
            futures[pool.submit(import_stock, fp, code, mkt)] = ("stock", code)
        for fp, code, name in index_files:
            futures[pool.submit(import_index, fp, code, name)] = ("index", code)

        for future in as_completed(futures):
            ftype, code = futures[future]
            try:
                if ftype == "index":
                    count, code = future.result()
                else:
                    count = future.result()
                total_records += count
                completed += 1
                if completed % 100 == 0:
                    logger.info("Progress: %d/%d, rows: %d", completed, total_files, total_records)
            except Exception as e:
                logger.error("%s %s failed: %s", ftype, code, e)
                completed += 1
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt, shutting down...")
        pool.shutdown(wait=True, cancel_futures=True)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    logger.info("Done. %d/%d files, %d rows total", completed, total_files, total_records)


if __name__ == "__main__":
    main()
