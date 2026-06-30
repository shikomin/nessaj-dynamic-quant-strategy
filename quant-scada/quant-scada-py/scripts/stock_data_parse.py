
import os
import sys
import struct
import logging
from pathlib import Path
from datetime import datetime, timedelta
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

# 数据目录
stock_data_root_path = Path(__file__).parent / "vipdoc"
stock_sh_data_path = stock_data_root_path / "sh" / "minline"
stock_sz_data_path = stock_data_root_path / "sz" / "minline"
stock_bj_data_path = stock_data_root_path / "bj" / "minline"

# 每条记录固定32字节
RECORD_SIZE = 32
# 字段格式：小端字节序
STRUCT_FORMAT = '<HHfffffII'

# def __init__(self, date: int, minute: str,
#                 open: float,high:float,low:float,close:float,
#                 amount:float,volume:int,reserved:int):
#     self.date = date
#     self.minute = minute
#     self.open = open
#     self.high = high
#     self.low = low
#     self.close = close
#     self.amount = amount
#     self.volume = volume
#     self.reserved = reserved

def parse_record(stock_code:str,data:bytes):
    if len(data) != RECORD_SIZE:
        raise ValueError(f"数据长度应为{RECORD_SIZE}字节，实际为{len(data)}字节")
    # 解包二进制数据
    (date_num, minute_num, open_price, high_price, 
        low_price, close_price, amount, volume, reserved) = struct.unpack(
        STRUCT_FORMAT, data
    )
    # 解码日期字段（通达信的特殊编码）
    year = date_num // 2048 + 2004
    month = (date_num % 2048) // 100
    day = (date_num % 2048) % 100
    
    # 计算时分秒（从当天的分钟数推算）
    hours = minute_num // 60
    minutes = minute_num % 60
    
    try:
        dt = datetime(year, month, day, hours, minutes)
    except ValueError:
        # 处理可能的无效日期
        dt = None
        logger.warning(f"无效日期: {year}-{month:02d}-{day:02d} {hours:02d}:{minutes:02d}")
    
    return {
        'stock_code': stock_code,
        'datetime': dt,
        'date': f"{year}-{month:02d}-{day:02d}",
        'time': f"{hours:02d}:{minutes:02d}",
        'minute': minute_num,
        'open': open_price,
        'high': high_price,
        'low': low_price,
        'close': close_price,
        'amount': amount,  # 成交额（元）
        'volume': volume,  # 成交量（股）
        'reserved': reserved
    }







def main():
    logger.info(stock_data_root_path)
    logger.info(stock_sh_data_path)
    logger.info(stock_sz_data_path)
    logger.info(stock_bj_data_path)
    records = []
    with open("G:/projects/NESSAJ/test/vipdoc/sz/minline/sz000001.lc1", 'rb') as f:
        stock_code = "sz000001"
        # 获取文件大小，计算记录数
        file_size = f.seek(0, os.SEEK_END)
        f.seek(0)
        
        record_count = file_size // RECORD_SIZE
        logger.info(f"文件 {stock_code} 包含 {record_count} 条记录")
        
        # 逐条读取并解析
        for i in range(record_count):
            data = f.read(RECORD_SIZE)
            if len(data) < RECORD_SIZE:
                break
            
            try:
                record = parse_record(stock_code,data)
                logger.info(f"历史数据{record}")
                records.append(record)
                
            except Exception as e:
                e.with_traceback()
                logger.error(f"解析第{i}条记录失败: {e}")
                continue
    
    if not records:
        logger.warning(f"股票 {stock_code} 没有符合日期范围的数据")
        return {}






if __name__ == "__main__":
    main()
    logger.info("Stock 1min data load...")