"""速率限制器 — zzshare API 调用限流"""
import time


class RateLimiter:
    """滑动窗口令牌桶"""

    def __init__(self, rate_per_minute: int):
        self.rate = rate_per_minute
        self._window: list[float] = []

    def acquire(self):
        now = time.time()
        self._window = [t for t in self._window if now - t < 60]
        if len(self._window) >= self.rate:
            wait = 60 - (now - self._window[0]) + 0.3
            time.sleep(wait)
            self._window = [t for t in self._window if time.time() - t < 60]
        self._window.append(time.time())

    @property
    def remaining(self) -> int:
        self._window = [t for t in self._window if time.time() - t < 60]
        return max(0, self.rate - len(self._window))
