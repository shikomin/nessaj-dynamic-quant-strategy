import logging
import time
from datetime import datetime, time as dt_time
from concurrent.futures import ThreadPoolExecutor, as_completed

from data.source import ZZShareClient
from data.writer import TDWriter
from config.settings import Config

logger = logging.getLogger(__name__)

FETCH_THREADS = 6


def _batch_list(lst, n):
    k, m = divmod(len(lst), n)
    batches = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        batches.append(lst[start:start + size])
        start += size
    return batches


class Collector:

    def __init__(self, stock_codes=None, index_codes=None):
        self.client = ZZShareClient()
        self.writer = TDWriter()
        self.stock_codes = stock_codes or []
        self.index_codes = index_codes or Config.INDEX_CODES
        self._stock_count = 0
        self._index_count = 0
        self._last_collect = None
        self._last_error = None
        self._sentiment_interval = 5 * 60  # 5 分钟
        self._last_sentiment = 0
        self._paused = False

    def is_trading_today(self):
        return self.client.is_trading_day()

    def _is_trading_time(self):
        now = datetime.now().time()
        return (dt_time(9, 30) <= now <= dt_time(11, 30)) or \
               (dt_time(13, 0) <= now <= dt_time(15, 0))

    def collect_realtime(self):
        if not self.is_trading_today() or not self._is_trading_time():
            if not self._paused:
                self._paused = True
                logger.info("Collection paused (outside trading hours, waiting for next session...)")
            return
        if self._paused:
            self._paused = False
            logger.info("Collection resumed (entered trading hours)")

        t0 = time.time()
        try:
            # 1. 指数
            if self.index_codes:
                data = self.client.get_realtime(self.index_codes)
                if data and "list" in data:
                    self.writer.write_index_rt(data["list"])
                    self._index_count = len(data["list"])

            # 2. 股票分批并行拉取
            if self.stock_codes:
                batches = _batch_list(self.stock_codes, Config.BATCH_COUNT)
                all_records = []

                def _fetch_one(batch):
                    for attempt in range(3):
                        data = self.client.get_realtime(batch)
                        if data and "list" in data:
                            return data["list"]
                        time.sleep(1)
                    return []

                with ThreadPoolExecutor(max_workers=FETCH_THREADS) as pool:
                    futures = {pool.submit(_fetch_one, b): i for i, b in enumerate(batches) if b}
                    for future in as_completed(futures):
                        try:
                            records = future.result()
                            if records:
                                all_records.extend(records)
                        except Exception as e:
                            logger.error("fetch batch failed: %s", e)

                if all_records:
                    self.writer.write_stock_rt_batch(all_records)
                    self._stock_count = len(all_records)

            # 3. 情绪 (5分钟)
            now_ts = time.time()
            if now_ts - self._last_sentiment >= self._sentiment_interval:
                self._last_sentiment = now_ts
                self._collect_sentiment_inner()

            self._last_collect = datetime.now().isoformat()
            self._last_error = None
            elapsed = time.time() - t0
            logger.info("collect_realtime: stocks=%d, indexes=%d, %.2fs",
                        self._stock_count, self._index_count, elapsed)

        except Exception as e:
            self._last_error = str(e)
            logger.error("collect_realtime failed: %s", e)

    def _collect_sentiment_inner(self):
        try:
            sentiment = self.client.get_market_sentiment()
            updown = self.client.get_updown_distribution()

            sd = sentiment.get("list", [{}])[0] if sentiment and "list" in sentiment else {}
            ud = updown.get("list", [{}])[0] if updown and "list" in updown else {}

            if sd or ud:
                self.writer.write_sentiment_rt(sd, ud)
                self.writer.write_sentiment_hist(sd, ud)
        except Exception:
            pass

    def collect_hist_kline(self, trade_date=None):
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y%m%d")

        try:
            for code in self.stock_codes[:50]:
                data = self.client.get_minute_kline(code, trade_date)
                if data and "list" in data:
                    self.writer.write_hist_kline(data["list"], is_index=False)

            for code in self.index_codes:
                data = self.client.get_minute_kline(code, trade_date)
                if data and "list" in data:
                    self.writer.write_hist_kline(data["list"], is_index=True)

        except Exception as e:
            logger.error("collect_hist_kline failed: %s", e)

    def get_status(self):
        return {
            "last_collect": self._last_collect,
            "last_error": self._last_error,
            "stock_count": self._stock_count,
            "index_count": self._index_count,
            "total_stocks": len(self.stock_codes),
            "index_codes": self.index_codes,
            "is_trading_day": self.is_trading_today()
        }
