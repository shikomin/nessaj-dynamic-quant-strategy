import logging
import time
from datetime import datetime, time as dt_time

from data.source import AlphaFeedSource
from data.writer import TDWriter
from config.settings import Config

logger = logging.getLogger(__name__)


class AlphaFeedCollector:

    def __init__(self, trade_day_checker=None):
        self.source = AlphaFeedSource()
        self.writer = TDWriter()
        self._trade_day_checker = trade_day_checker
        self._last_collect = None
        self._last_error = None
        self._count = 0

    def _is_trading_time(self):
        now = datetime.now().time()
        return (dt_time(9, 30) <= now <= dt_time(11, 30)) or \
               (dt_time(13, 0) <= now <= dt_time(15, 1))

    def _is_trading_day(self):
        if self._trade_day_checker:
            return self._trade_day_checker()
        today = datetime.now()
        return today.weekday() < 5

    def collect_realtime(self):
        if not self._is_trading_day() or not self._is_trading_time():
            return

        t0 = time.time()
        try:
            quotes = self.source.get_cn_stock_realtime(to_dataframe=False)
            if not quotes:
                logger.warning("AlphaFeed returned no quotes")
                return

            written = self.writer.write_stock_rt_alphafeed(quotes)
            self._count = written
            self._last_collect = datetime.now().isoformat()
            self._last_error = None
            elapsed = time.time() - t0
            logger.info("alphafeed collect_realtime: fetched=%d, written=%d, %.2fs",
                        len(quotes), written, elapsed)

        except Exception as e:
            self._last_error = str(e)
            logger.error("alphafeed collect_realtime failed: %s", e)

    def collect_once(self):
        """手动触发一次全量拉取"""
        t0 = time.time()
        try:
            quotes = self.source.get_cn_stock_realtime(to_dataframe=False)
            if not quotes:
                return {"status": "empty", "count": 0}

            written = self.writer.write_stock_rt_alphafeed(quotes)
            self._count = written
            self._last_collect = datetime.now().isoformat()
            self._last_error = None
            elapsed = time.time() - t0
            logger.info("alphafeed collect_once: fetched=%d, written=%d, %.2fs",
                        len(quotes), written, elapsed)
            return {
                "status": "ok",
                "fetched": len(quotes),
                "written": written,
                "elapsed": round(elapsed, 2)
            }
        except Exception as e:
            self._last_error = str(e)
            logger.error("alphafeed collect_once failed: %s", e)
            return {"status": "error", "message": str(e)}

    def flush_to_hist(self):
        """22:00 定时任务：判断交易日 → 刷当天实时数据到历史库"""
        if not self._is_trading_day():
            logger.info("flush_to_hist: %s is not a trading day, skipped",
                        datetime.now().strftime("%Y-%m-%d"))
            return {"status": "skipped", "reason": "not trading day"}

        trade_date = datetime.now().strftime("%Y-%m-%d")
        logger.info("flush_to_hist: starting flush for %s", trade_date)
        t0 = time.time()
        try:
            result = self.writer.flush_rt_to_hist(trade_date)
            elapsed = time.time() - t0
            logger.info("flush_to_hist: done stocks=%s indexes=%s, %.2fs",
                        result.get("stocks"), result.get("indexes"), elapsed)
            result["status"] = "ok"
            result["elapsed"] = round(elapsed, 2)
            return result
        except Exception as e:
            logger.error("flush_to_hist failed: %s", e)
            return {"status": "error", "message": str(e)}

    def get_status(self):
        return {
            "last_collect": self._last_collect,
            "last_error": self._last_error,
            "count": self._count
        }
