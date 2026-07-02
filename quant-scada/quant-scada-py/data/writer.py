import taosws
import logging
import threading
from datetime import datetime
from queue import LifoQueue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed
from config.settings import Config

logger = logging.getLogger(__name__)

WRITE_THREADS = 8
BATCH_CHUNK_SIZE = 250


# ── 连接池 ────────────────────────────────────────────────────

class _ConnPool:

    def __init__(self, dsn, size=8):
        self._dsn = dsn
        self._size = size
        self._q = LifoQueue(maxsize=size)

    def get(self):
        try:
            return self._q.get_nowait()
        except Empty:
            return taosws.connect(self._dsn)

    def put(self, conn, healthy=True):
        if not healthy:
            try:
                conn.close()
            except Exception:
                pass
            return
        try:
            self._q.put_nowait(conn)
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def close_all(self):
        while True:
            try:
                conn = self._q.get_nowait()
                conn.close()
            except Empty:
                break


# ── 公共工具函数 ──────────────────────────────────────────────

def _parse_symbol(symbol):
    if "." in symbol:
        parts = symbol.split(".")
        return parts[0], parts[-1]
    return symbol, ""


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _safe(v):
    return v if v else 0


# ── TDWriter ──────────────────────────────────────────────────

class TDWriter:

    def __init__(self):
        dsn = Config.TD_DSN
        self._pool = _ConnPool(dsn, size=WRITE_THREADS)
        self.rt_db = Config.RT_DATABASE
        self.hist_db = Config.HIST_DATABASE

    # ── 连接管理 ─────────────────────────────────────────────

    def _get_conn(self):
        return self._pool.get()

    def _put_conn(self, conn, healthy=True):
        self._pool.put(conn, healthy)

    # ── 批量执行 ─────────────────────────────────────────────

    def _execute_batch(self, conn, parts, stable_label="batch"):
        written = 0
        for i in range(0, len(parts), BATCH_CHUNK_SIZE):
            chunk = parts[i:i + BATCH_CHUNK_SIZE]
            sql = f"INSERT INTO {' '.join(chunk)}"
            try:
                conn.execute(sql)
                written += len(chunk)
            except Exception as e:
                logger.error("%s batch write failed: %s", stable_label, e)
        return written

    # ── zzshare: 股票实时批量 ────────────────────────────────

    def write_stock_rt_batch(self, records):
        if not records:
            return
        now = _now_str()
        chunk_size = max(1, len(records) // WRITE_THREADS)
        chunks = [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)]

        def _write_chunk(chunk):
            conn = self._get_conn()
            try:
                count = 0
                for r in chunk:
                    ts_code = r.get("ts_code", "")
                    code, market = _parse_symbol(ts_code)
                    table_name = f"rt_{market}_{code}" if market else f"rt_{code}"
                    sql = (
                        f"INSERT INTO {self.rt_db}.{table_name} "
                        f"USING {self.rt_db}.stock_rt_data "
                        f"TAGS ('{ts_code}', '{r.get('name', '')}', '{market}') "
                        f"VALUES ("
                        f"'{now}', "
                        f"{_safe(r.get('close', 0))}, "
                        f"{_safe(r.get('open', 0))}, "
                        f"{_safe(r.get('high', 0))}, "
                        f"{_safe(r.get('low', 0))}, "
                        f"{_safe(r.get('pre_close', 0))}, "
                        f"{_safe(r.get('quote_rate', 0))}, "
                        f"{_safe(r.get('change_amount', 0))}, "
                        f"{_safe(r.get('vol', 0))}, "
                        f"{_safe(r.get('amount', 0))}, "
                        f"{_safe(r.get('turnover_rate', 0))}, "
                        f"{_safe(r.get('amplitude', 0))}"
                        f")"
                    )
                    try:
                        conn.execute(sql)
                        count += 1
                    except Exception:
                        pass
                return count
            finally:
                self._put_conn(conn)

        total = 0
        with ThreadPoolExecutor(max_workers=WRITE_THREADS) as pool:
            futures = {pool.submit(_write_chunk, chunk): i for i, chunk in enumerate(chunks) if chunk}
            for future in as_completed(futures):
                try:
                    total += future.result()
                except Exception as e:
                    logger.error("write chunk failed: %s", e)

    # ── zzshare: 指数实时 ────────────────────────────────────

    def write_index_rt(self, records):
        if not records:
            return
        conn = self._get_conn()
        try:
            now = _now_str()
            for r in records:
                ts_code = r.get("ts_code", "")
                code, market = _parse_symbol(ts_code)
                table_name = f"idx_rt_{market}_{code}" if market else f"idx_rt_{code}"
                sql = (
                    f"INSERT INTO {self.rt_db}.{table_name} "
                    f"USING {self.rt_db}.index_rt_data "
                    f"TAGS ('{ts_code}', '{r.get('name', '')}', '{market}') "
                    f"VALUES ("
                    f"'{now}', "
                    f"{_safe(r.get('close', 0))}, "
                    f"{_safe(r.get('open', 0))}, "
                    f"{_safe(r.get('high', 0))}, "
                    f"{_safe(r.get('low', 0))}, "
                    f"{_safe(r.get('pre_close', 0))}, "
                    f"{_safe(r.get('quote_rate', 0))}, "
                    f"{_safe(r.get('change_amount', 0))}, "
                    f"{_safe(r.get('vol', 0))}, "
                    f"{_safe(r.get('amount', 0))}"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass
            self._put_conn(conn, healthy=True)
        except Exception:
            self._put_conn(conn, healthy=False)

    # ── 情绪数据 ─────────────────────────────────────────────

    def write_sentiment_rt(self, sentiment_data, updown_data):
        conn = self._get_conn()
        try:
            now = _now_str()
            if sentiment_data:
                s = sentiment_data
                sql = (
                    f"INSERT INTO {self.rt_db}.sent_rt_overall "
                    f"USING {self.rt_db}.intraday_sentiment "
                    f"TAGS ('overall') VALUES ("
                    f"'{now}', {_safe(s.get('sentiment_score', 0))}, "
                    f"0, 0, 0, 0, 0, 0, 0, 0, 0, 0"
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
                    f"TAGS ('updown') VALUES ("
                    f"'{now}', 0, "
                    f"{_safe(u.get('up_count', 0))}, "
                    f"{_safe(u.get('down_count', 0))}, "
                    f"{_safe(u.get('flat_count', 0))}, "
                    f"{_safe(u.get('limit_up_count', 0))}, "
                    f"{_safe(u.get('limit_down_count', 0))}, "
                    f"{_safe(u.get('up_gt_7pct', 0))}, "
                    f"{_safe(u.get('down_gt_7pct', 0))}, "
                    f"0, 0, 0"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass
            self._put_conn(conn, healthy=True)
        except Exception:
            self._put_conn(conn, healthy=False)

    def write_sentiment_hist(self, sentiment_data, updown_data):
        conn = self._get_conn()
        try:
            now = _now_str()
            if sentiment_data:
                s = sentiment_data
                sql = (
                    f"INSERT INTO {self.hist_db}.sent_overall "
                    f"USING {self.hist_db}.market_hist_sentiment "
                    f"TAGS ('overall') VALUES ("
                    f"'{now}', {_safe(s.get('sentiment_score', 0))}, "
                    f"0, 0, 0, 0, 0, 0, 0, 0, 0, 0"
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
                    f"TAGS ('updown') VALUES ("
                    f"'{now}', 0, "
                    f"{_safe(u.get('up_count', 0))}, "
                    f"{_safe(u.get('down_count', 0))}, "
                    f"{_safe(u.get('flat_count', 0))}, "
                    f"{_safe(u.get('limit_up_count', 0))}, "
                    f"{_safe(u.get('limit_down_count', 0))}, "
                    f"{_safe(u.get('up_gt_7pct', 0))}, "
                    f"{_safe(u.get('down_gt_7pct', 0))}, "
                    f"0, 0, 0"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass
            self._put_conn(conn, healthy=True)
        except Exception:
            self._put_conn(conn, healthy=False)

    # ── zzshare: 历史K线 ─────────────────────────────────────

    def write_hist_kline(self, records, is_index=False):
        if not records:
            return
        conn = self._get_conn()
        try:
            prefix = "idx_hist_" if is_index else "hist_"
            stable = "index_hist_kline_1m" if is_index else "stock_hist_kline_1m"
            for r in records:
                raw_code = r.get("code", "")
                ts_code = r.get("ts_code", "")
                code = raw_code
                market = ts_code.split(".")[-1] if "." in ts_code else ""
                table_name = f"{prefix}{market}_{code}" if market else f"{prefix}{code}"
                trade_time = r.get("trade_time", "")
                if len(trade_time) == 12:
                    ts = f"{trade_time[:4]}-{trade_time[4:6]}-{trade_time[6:8]} {trade_time[8:10]}:{trade_time[10:12]}:00.000"
                else:
                    ts = _now_str()
                extra_tags = ""
                if is_index:
                    extra_tags = f", '{r.get('name', '')}'"
                else:
                    extra_tags = f", '{market}'"
                sql = (
                    f"INSERT INTO {self.hist_db}.{table_name} "
                    f"USING {self.hist_db}.{stable} "
                    f"TAGS ('{code}'{extra_tags}) "
                    f"VALUES ("
                    f"'{ts}', "
                    f"{_safe(r.get('open', 0))}, "
                    f"{_safe(r.get('high', 0))}, "
                    f"{_safe(r.get('low', 0))}, "
                    f"{_safe(r.get('close', 0))}, "
                    f"{_safe(r.get('vol', 0))}, "
                    f"{_safe(r.get('amount', 0))}"
                    f")"
                )
                try:
                    conn.execute(sql)
                except Exception:
                    pass
            self._put_conn(conn, healthy=True)
        except Exception:
            self._put_conn(conn, healthy=False)

    # ── AlphaFeed: 实时行情批量 ──────────────────────────────

    def write_stock_rt_alphafeed(self, quotes):
        if not quotes:
            return 0
        now = _now_str()
        conn = self._get_conn()
        try:
            stock_parts = []
            index_parts = []
            for q in quotes:
                symbol = q.get("symbol", "")
                code, market = _parse_symbol(symbol)
                ext = q.get("ext", {}) or {}
                name = (ext.get("name", "") or "").replace("'", "\\'")
                qtype = (ext.get("type", "") or "").lower()

                if "index" in qtype:
                    table_name = f"idx_rt_{market}_{code}" if market else f"idx_rt_{code}"
                    part = (
                        f"{self.rt_db}.{table_name} "
                        f"USING {self.rt_db}.index_rt_data "
                        f"TAGS ('{symbol}', '{name}', '{market}') "
                        f"VALUES ("
                        f"'{now}', "
                        f"{_safe(q.get('last_price', 0))}, "
                        f"{_safe(q.get('open', 0))}, "
                        f"{_safe(q.get('high', 0))}, "
                        f"{_safe(q.get('low', 0))}, "
                        f"{_safe(q.get('prev_close', 0))}, "
                        f"{_safe(ext.get('change_pct', 0))}, "
                        f"{_safe(ext.get('change_amount', 0))}, "
                        f"{_safe(q.get('volume', 0))}, "
                        f"{_safe(q.get('amount', 0))}"
                        f")"
                    )
                    index_parts.append(part)
                else:
                    table_name = f"rt_{market}_{code}" if market else f"rt_{code}"
                    part = (
                        f"{self.rt_db}.{table_name} "
                        f"USING {self.rt_db}.stock_rt_data "
                        f"TAGS ('{symbol}', '{name}', '{market}') "
                        f"VALUES ("
                        f"'{now}', "
                        f"{_safe(q.get('last_price', 0))}, "
                        f"{_safe(q.get('open', 0))}, "
                        f"{_safe(q.get('high', 0))}, "
                        f"{_safe(q.get('low', 0))}, "
                        f"{_safe(q.get('prev_close', 0))}, "
                        f"{_safe(ext.get('change_pct', 0))}, "
                        f"{_safe(ext.get('change_amount', 0))}, "
                        f"{_safe(q.get('volume', 0))}, "
                        f"{_safe(q.get('amount', 0))}, "
                        f"{_safe(ext.get('turnover_rate', 0))}, "
                        f"{_safe(ext.get('amplitude', 0))}"
                        f")"
                    )
                    stock_parts.append(part)

            written = 0
            for stable_label, parts in [("stock", stock_parts), ("index", index_parts)]:
                if parts:
                    written += self._execute_batch(conn, parts, stable_label)
            self._put_conn(conn, healthy=True)
            return written
        except Exception as e:
            logger.error("alphafeed batch write error: %s", e)
            self._put_conn(conn, healthy=False)
            return 0

    # ── 每日刷历史 ───────────────────────────────────────────

    def flush_rt_to_hist(self, trade_date_str):
        from datetime import datetime as dt, timedelta
        today_start = f"{trade_date_str} 00:00:00.000"
        next_day = dt.strptime(trade_date_str, "%Y-%m-%d") + timedelta(days=1)
        today_end = next_day.strftime("%Y-%m-%d 00:00:00.000")

        conn = self._get_conn()
        stock_count = 0
        index_count = 0
        try:
            result = conn.execute(f"SHOW {self.rt_db}.STABLES")
            table_names = []
            if result:
                for row in result:
                    if isinstance(row, (list, tuple)) and len(row) > 0:
                        table_names.append(str(row[0]))
                    elif hasattr(row, "tbname"):
                        table_names.append(str(row.tbname))

            for tbl in table_names:
                if tbl.startswith("rt_"):
                    hist_tbl, code, market = self._parse_rt_table(tbl, prefix="rt_")
                    sql = (
                        f"INSERT INTO {self.hist_db}.{hist_tbl} "
                        f"USING {self.hist_db}.stock_hist_kline_1m "
                        f"TAGS ('{code}', '{market}') "
                        f"SELECT ts, open, high, low, price AS close, volume, amount "
                        f"FROM {self.rt_db}.{tbl} "
                        f"WHERE ts >= '{today_start}' AND ts < '{today_end}'"
                    )
                    try:
                        conn.execute(sql)
                        stock_count += 1
                    except Exception as e:
                        logger.error("flush stock %s failed: %s", tbl, e)

                elif tbl.startswith("idx_rt_"):
                    hist_tbl, code, market = self._parse_rt_table(tbl, prefix="idx_rt_")
                    sql = (
                        f"INSERT INTO {self.hist_db}.{hist_tbl} "
                        f"USING {self.hist_db}.index_hist_kline_1m "
                        f"TAGS ('{code}', '{code}') "
                        f"SELECT ts, open, high, low, price AS close, volume, amount "
                        f"FROM {self.rt_db}.{tbl} "
                        f"WHERE ts >= '{today_start}' AND ts < '{today_end}'"
                    )
                    try:
                        conn.execute(sql)
                        index_count += 1
                    except Exception as e:
                        logger.error("flush index %s failed: %s", tbl, e)

            logger.info("flush_to_hist: %s stocks=%d indexes=%d",
                        trade_date_str, stock_count, index_count)
            self._put_conn(conn, healthy=True)
            return {"stocks": stock_count, "indexes": index_count}
        except Exception as e:
            logger.error("flush_to_hist error: %s", e)
            self._put_conn(conn, healthy=False)
            return {"stocks": 0, "indexes": 0}

    def _parse_rt_table(self, tbl, prefix):
        parts_str = tbl[len(prefix):]
        if "_" in parts_str:
            idx = parts_str.index("_")
            market = parts_str[:idx]
            code = parts_str[idx + 1:]
        else:
            market, code = "", parts_str
        hist_prefix = "hist_" if prefix.startswith("rt_") else "idx_hist_"
        hist_tbl = f"{hist_prefix}{market}_{code}" if market else f"{hist_prefix}{code}"
        return hist_tbl, code, market

    # ── 关闭 ─────────────────────────────────────────────────

    def close(self):
        self._pool.close_all()
