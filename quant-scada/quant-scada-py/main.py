import logging
from pathlib import Path
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv(Path(__file__).parent / "config" / ".env")

from config.settings import Config
from core.collector import Collector
from core.alpha_collector import AlphaFeedCollector
from data.stock_loader import load_stocks_from_mysql
from data.source import ZZShareClient
from component.routes import init_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logger.info("Starting quant-scada-py data collector...")

    scheduler = BackgroundScheduler()

    alpha_collector = None
    collector = None

    if Config.ALPHAFEED_API_KEY:
        trade_client = ZZShareClient()
        alpha_collector = AlphaFeedCollector(
            trade_day_checker=trade_client.is_trading_day
        )
        scheduler.add_job(
            alpha_collector.collect_realtime,
            "interval",
            seconds=Config.ALPHAFEED_COLLECT_INTERVAL,
            id="alphafeed_realtime",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1
        )
        logger.info("alphafeed scheduler: interval=%ds", Config.ALPHAFEED_COLLECT_INTERVAL)

        scheduler.add_job(
            alpha_collector.flush_to_hist,
            "cron",
            hour=22,
            minute=0,
            id="alphafeed_flush_hist",
            replace_existing=True
        )
        logger.info("alphafeed flush hist scheduler: daily at 22:00")

        logger.info("Pre-warming: creating all subtables...")
        alpha_collector.collect_once()
        logger.info("Pre-warm complete")

    else:
        logger.warning("ALPHAFEED_API_KEY not set, falling back to zzshare")
        stock_codes = load_stocks_from_mysql()
        if not stock_codes:
            stock_codes = Config.STOCK_CODES
            logger.warning("MySQL load failed, using env STOCK_CODES: %d", len(stock_codes))

        collector = Collector(stock_codes=stock_codes)
        if collector.is_trading_today():
            logger.info("Today is trading day, starting realtime collection")
        else:
            logger.info("Today is NOT a trading day, collection paused")

        scheduler.add_job(
            collector.collect_realtime,
            "interval",
            seconds=Config.COLLECT_INTERVAL,
            id="zzshare_realtime",
            replace_existing=True
        )
        logger.info("zzshare scheduler: interval=%ds, batches=%d, total_stocks=%d",
                    Config.COLLECT_INTERVAL, Config.BATCH_COUNT, len(stock_codes))

    scheduler.start()
    logger.info("Scheduler started")

    app = init_routes(collector, alpha_collector)
    try:
        app.run(host=Config.FLASK_HOST, port=Config.FLASK_PORT, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        scheduler.shutdown()
        if collector:
            collector.writer.close()
        if alpha_collector:
            alpha_collector.source.close()
