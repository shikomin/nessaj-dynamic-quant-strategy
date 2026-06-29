import logging
import time
from datetime import date
from zzshare.client import DataApi
from config.settings import Config

logger = logging.getLogger(__name__)


class ZZShareClient:

    def __init__(self, token=None):
        token = token or Config.ZZSHARE_TOKEN
        if token == "anonymous":
            token = None
        self._api = DataApi(token=token) if token else DataApi()
        self._trade_days_cache = None
        self._cache_date = None

    def get_realtime(self, codes):
        """获取实时行情，codes 为 ts_code 列表"""
        ts_code = ",".join(codes)
        df = self._api.rt_k(ts_code=ts_code)
        if df is None or df.empty:
            return None
        return {"list": df.to_dict(orient="records")}

    def get_market_sentiment(self):
        """获取市场情绪"""
        try:
            df = self._api.market_sentiment()
            if df is not None and not df.empty:
                return {"list": df.to_dict(orient="records")}
        except Exception as e:
            logger.warning("market_sentiment failed: %s", e)
        return None

    def get_updown_distribution(self):
        """获取涨跌分布"""
        try:
            df = self._api.updown_distribution()
            if df is not None and not df.empty:
                return {"list": df.to_dict(orient="records")}
        except Exception as e:
            logger.warning("updown_distribution failed: %s", e)
        return None

    def get_minute_kline(self, code, trade_time, freq="1min"):
        try:
            df = self._api.stk_mins(ts_code=code, trade_time=trade_time, freq=freq)
            if df is not None and not df.empty:
                return {"list": df.to_dict(orient="records")}
        except Exception as e:
            logger.warning("stk_mins failed for %s: %s", code, e)
        return None

    def is_trading_day(self, dt=None):
        """判断今天 (或指定日期) 是否为交易日"""
        if dt is None:
            dt = date.today()
        today_str = dt.strftime("%Y%m%d")

        if self._cache_date != dt:
            try:
                result = self._api.trade_days(days=30)
                if result is not None:
                    if hasattr(result, "empty") and result.empty:
                        self._trade_days_cache = set()
                    elif isinstance(result, list):
                        self._trade_days_cache = set()
                        for d in result:
                            if hasattr(d, "strftime"):
                                self._trade_days_cache.add(d.strftime("%Y%m%d"))
                            elif isinstance(d, str):
                                self._trade_days_cache.add(d.replace("-", ""))
                    else:
                        col = result.columns[0] if len(result.columns) > 0 else None
                        if col:
                            self._trade_days_cache = set(
                                result[col].apply(lambda x: x.strftime("%Y%m%d") if hasattr(x, "strftime") else str(x).replace("-", "")).tolist()
                            )
                self._cache_date = dt
            except Exception as e:
                logger.warning("trade_days failed: %s, assuming trading day", e)
                return True

        if self._trade_days_cache is None:
            return True
        return today_str in self._trade_days_cache
