import taosws
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from config.settings import Config

logger = logging.getLogger(__name__)

# 写线程数
WRITE_THREADS = 8


class TDWriter:

    def __init__(self):
        self._raw_dsn = Config.TD_DSN
        self.rt_db = Config.RT_DATABASE
        self.hist_db = Config.HIST_DATABASE

    def _build_dsn(self):
        return self._raw_dsn

    def _new_conn(self):
        return taosws.connect(self._build_dsn())

    def write_stock_rt_batch(self, records):
        """多线程批量写入股票实时数据"""
        if not records:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        chunk_size = max(1, len(records) // WRITE_THREADS)
        chunks = [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)]

        def _write_chunk(chunk):
            conn = self._new_conn()
            try:
                count = 0
                for r in chunk:
                    code = r.get("ts_code", "").replace(".", "_")
                    sql = (
                        f"INSERT INTO {self.rt_db}.rt_{code} "
                        f"USING {self.rt_db}.stock_rt_data "
                        f"TAGS ('{r.get('ts_code','')}', '{r.get('name','')}', '{r.get('ts_code','').split('.')[-1] if '.' in r.get('ts_code','') else ''}') "
                        f"VALUES ("
                        f"'{now}', "
                        f"{r.get('close', 0) or 0}, "
                        f"{r.get('open', 0) or 0}, "
                        f"{r.get('high', 0) or 0}, "
                        f"{r.get('low', 0) or 0}, "
                        f"{r.get('pre_close', 0) or 0}, "
                        f"{r.get('quote_rate', 0) or 0}, "
                        f"{r.get('vol', 0) or 0}, "
                        f"{r.get('amount', 0) or 0}, "
                        f"{r.get('turnover_rate', 0) or 0}, "
                        f"{r.get('ttm_pe_rate', 0) or 0}, "
                        f"{r.get('eps_ttm', 0) or 0}, "
                        f"{r.get('market_value', 0) or 0}, "
                        f"{r.get('circulation_value', 0) or 0}, "
                        f"{_parse_grp_first(r.get('bid_grp', ''), 0)}, "
                        f"{_parse_grp_first(r.get('bid_grp', ''), 1)}, "
                        f"{_parse_grp_first(r.get('offer_grp', ''), 0)}, "
                        f"{_parse_grp_first(r.get('offer_grp', ''), 1)}, "
                        f"{r.get('auction_vol', 0) or 0}, "
                        f"{r.get('auction_val', 0) or 0}, "
                        f"{r.get('auction_px', 0) or 0}, "
                        f"0"
                        f")"
                    )
                    try:
                        conn.execute(sql)
                        count += 1
                    except Exception:
                        pass
                return count
            finally:
                conn.close()

        total = 0
        with ThreadPoolExecutor(max_workers=WRITE_THREADS) as pool:
            futures = {pool.submit(_write_chunk, chunk): i for i, chunk in enumerate(chunks) if chunk}
            for future in as_completed(futures):
                try:
                    total += future.result()
                except Exception as e:
                    logger.error("write chunk failed: %s", e)

    def write_index_rt(self, records):
        if not records:
            return
        conn = self._new_conn()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            for r in records:
                code = r.get("ts_code", "").replace(".", "_")
                sql = (
                    f"INSERT INTO {self.rt_db}.idx_rt_{code} "
                    f"USING {self.rt_db}.index_rt_data "
                    f"TAGS ('{r.get('ts_code','')}', '{r.get('name','')}', '{r.get('ts_code','').split('.')[-1] if '.' in r.get('ts_code','') else ''}') "
                    f"VALUES ("
                    f"'{now}', "
                    f"{r.get('close', 0) or 0}, "
                    f"{r.get('open', 0) or 0}, "
                    f"{r.get('high', 0) or 0}, "
                    f"{r.get('low', 0) or 0}, "
                    f"{r.get('pre_close', 0) or 0}, "
                    f"{r.get('quote_rate', 0) or 0}, "
                    f"{r.get('vol', 0) or 0}, "
                    f"{r.get('amount', 0) or 0}"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass
        finally:
            conn.close()

    def write_sentiment_rt(self, sentiment_data, updown_data):
        """写入日内情绪到实时数据库"""
        conn = self._new_conn()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

            if sentiment_data:
                s = sentiment_data
                sql = (
                    f"INSERT INTO {self.rt_db}.sent_rt_overall "
                    f"USING {self.rt_db}.intraday_sentiment "
                    f"TAGS ('overall') "
                    f"VALUES ("
                    f"'{now}', "
                    f"{s.get('sentiment_score', 0) or 0}, "
                    f"0, 0, 0, 0, 0, 0, 0, "
                    f"0, 0, 0"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass

            if updown_data:
                u = updown_data
                sql = (
                    f"INSERT INTO {self.rt_db}.sent_rt_updown "
                    f"USING {self.rt_db}.intraday_sentiment "
                    f"TAGS ('updown') "
                    f"VALUES ("
                    f"'{now}', "
                    f"0, "
                    f"{u.get('up_count', 0) or 0}, "
                    f"{u.get('down_count', 0) or 0}, "
                    f"{u.get('flat_count', 0) or 0}, "
                    f"{u.get('limit_up_count', 0) or 0}, "
                    f"{u.get('limit_down_count', 0) or 0}, "
                    f"{u.get('up_gt_7pct', 0) or 0}, "
                    f"{u.get('down_gt_7pct', 0) or 0}, "
                    f"0, 0, 0"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass
        finally:
            conn.close()

    def write_sentiment_hist(self, sentiment_data, updown_data):
        """写入情绪到历史数据库"""
        conn = self._new_conn()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

            if sentiment_data:
                s = sentiment_data
                sql = (
                    f"INSERT INTO {self.hist_db}.sent_overall "
                    f"USING {self.hist_db}.market_hist_sentiment "
                    f"TAGS ('overall') "
                    f"VALUES ("
                    f"'{now}', "
                    f"{s.get('sentiment_score', 0) or 0}, "
                    f"0, 0, 0, 0, 0, 0, 0, "
                    f"0, 0, 0"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass

            if updown_data:
                u = updown_data
                sql = (
                    f"INSERT INTO {self.hist_db}.sent_updown "
                    f"USING {self.hist_db}.market_hist_sentiment "
                    f"TAGS ('updown') "
                    f"VALUES ("
                    f"'{now}', "
                    f"0, "
                    f"{u.get('up_count', 0) or 0}, "
                    f"{u.get('down_count', 0) or 0}, "
                    f"{u.get('flat_count', 0) or 0}, "
                    f"{u.get('limit_up_count', 0) or 0}, "
                    f"{u.get('limit_down_count', 0) or 0}, "
                    f"{u.get('up_gt_7pct', 0) or 0}, "
                    f"{u.get('down_gt_7pct', 0) or 0}, "
                    f"0, 0, 0"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass
        finally:
            conn.close()

    def write_hist_kline(self, records, is_index=False):
        if not records:
            return
        conn = self._new_conn()
        try:
            table_prefix = "idx_hk_" if is_index else "hk_"
            stable = "index_hist_kline_1m" if is_index else "stock_hist_kline_1m"
            for r in records:
                code = r.get("code", "").replace(".", "_")
                trade_time = r.get("trade_time", "")
                if len(trade_time) == 12:
                    ts = f"{trade_time[:4]}-{trade_time[4:6]}-{trade_time[6:8]} {trade_time[8:10]}:{trade_time[10:12]}:00.000"
                else:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.000")

                extra_tags = ""
                if is_index:
                    extra_tags = f", '{r.get('name', '')}'"
                else:
                    extra_tags = f", '{r.get('ts_code','').split('.')[-1] if '.' in r.get('ts_code','') else ''}'"

                sql = (
                    f"INSERT INTO {self.hist_db}.{table_prefix}{code} "
                    f"USING {self.hist_db}.{stable} "
                    f"TAGS ('{r.get('code', '')}'{extra_tags}) "
                    f"VALUES ("
                    f"'{ts}', "
                    f"{r.get('open', 0) or 0}, "
                    f"{r.get('high', 0) or 0}, "
                    f"{r.get('low', 0) or 0}, "
                    f"{r.get('close', 0) or 0}, "
                    f"{r.get('vol', 0) or 0}, "
                    f"{r.get('amount', 0) or 0}"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass
        finally:
            conn.close()

    def close(self):
        pass


def _parse_grp_first(grp_str, index):
    if not grp_str:
        return 0
    parts = grp_str.split(",")
    idx = index * 3
    if len(parts) > idx:
        try:
            return float(parts[idx])
        except ValueError:
            return 0
    return 0
