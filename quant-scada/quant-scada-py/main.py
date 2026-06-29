import logging
from pathlib import Path
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv(Path(__file__).parent / "config" / ".env")

from config.settings import Config
from core.collector import Collector
from data.stock_loader import load_stocks_from_mysql
from component.routes import init_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.info("Starting quant-scada-py data collector...")

    # 加载全量股票
    stock_codes = load_stocks_from_mysql()
    if not stock_codes:
        stock_codes = Config.STOCK_CODES
        logger.warning("MySQL load failed, using env STOCK_CODES: %d", len(stock_codes))

    collector = Collector(stock_codes=stock_codes)

    if collector.is_trading_today():
        logger.info("Today is trading day, starting realtime collection")
    else:
        logger.info("Today is NOT a trading day, collection paused")

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        collector.collect_realtime,
        "interval",
        seconds=Config.COLLECT_INTERVAL,
        id="realtime",
        replace_existing=True
    )
    scheduler.start()
    logger.info("Scheduler started: realtime=%ds, batches=%d, total_stocks=%d",
                Config.COLLECT_INTERVAL, Config.BATCH_COUNT, len(stock_codes))

    app = init_routes(collector)
    try:
        app.run(host=Config.FLASK_HOST, port=Config.FLASK_PORT, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown()
        collector.writer.close()
