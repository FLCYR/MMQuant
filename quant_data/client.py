"""Tushare 调用封装：令牌桶（滑动窗口）限流 + 指数退避重试 + 分页 + 日志。

全项目对 Tushare 的访问都应通过 TushareClient，不直接调用 ts.pro_api。
"""
from __future__ import annotations

import threading
import time
from collections import deque

import pandas as pd
import tushare as ts

import config
from quant_data.log import get_logger


class SlidingWindowLimiter:
    """滑动窗口限流：每 window 秒内最多 max_calls 次调用。"""

    def __init__(self, max_calls: int, window: float = 60.0):
        self.max_calls = max(1, int(max_calls))
        self.window = window
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = self.window - (now - self._calls[0]) + 0.01
            time.sleep(max(sleep_for, 0.01))


class TushareClient:
    def __init__(self, token: str | None = None):
        self.log = get_logger("quant_data.client")
        ts.set_token(token or config.TUSHARE_TOKEN)
        try:
            self.pro = ts.pro_api(timeout=config.HTTP_TIMEOUT)
        except TypeError:                     # 老版本 pro_api 无 timeout 形参
            self.pro = ts.pro_api()
        self._limiters: dict[str, SlidingWindowLimiter] = {}

    def _limiter(self, api_name: str) -> SlidingWindowLimiter:
        if api_name not in self._limiters:
            rate = config.RATE_LIMITS.get(api_name, config.DEFAULT_RATE_PER_MIN)
            self._limiters[api_name] = SlidingWindowLimiter(rate)
        return self._limiters[api_name]

    def call(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        paged: bool = False,
        page_size: int | None = None,
        **kwargs,
    ) -> pd.DataFrame:
        """调用一个 Tushare 接口。

        paged=True 时自动按 offset/limit 翻页，直到返回行数不足一页。
        """
        if not paged:
            return self._call_once(api_name, fields=fields, **kwargs)

        page_size = page_size or config.PAGE_SIZE
        frames: list[pd.DataFrame] = []
        offset = 0
        for _ in range(2000):  # 安全上限，防止接口忽略 offset 导致死循环
            chunk = self._call_once(
                api_name, fields=fields, offset=offset, limit=page_size, **kwargs
            )
            if chunk is None or chunk.empty:
                break
            frames.append(chunk)
            if len(chunk) < page_size:
                break
            offset += page_size
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _is_permanent(err: Exception) -> bool:
        """权限/参数类错误重试无意义，直接失败。"""
        msg = str(err)
        return ("没有接口" in msg) or ("权限" in msg) or ("permission" in msg.lower())

    def _call_once(self, api_name: str, *, fields: str | None = None, **kwargs) -> pd.DataFrame:
        last_err: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            self._limiter(api_name).acquire()
            try:
                if fields is not None:
                    df = self.pro.query(api_name, fields=fields, **kwargs)
                else:
                    df = self.pro.query(api_name, **kwargs)
                return df if df is not None else pd.DataFrame()
            except Exception as e:  # 网络/限流/接口异常统一退避重试
                last_err = e
                if self._is_permanent(e):
                    self.log.error("接口 %s 权限/参数错误，不重试：%s", api_name, e)
                    break
                backoff = config.RETRY_BACKOFF[min(attempt, len(config.RETRY_BACKOFF) - 1)]
                self.log.warning(
                    "接口 %s 第 %d 次调用失败: %s（%ds 后重试）",
                    api_name, attempt + 1, e, backoff,
                )
                time.sleep(backoff)
        raise RuntimeError(f"接口 {api_name} 调用失败: {last_err}")


# 进程内共享单例，避免重复建立 limiter / pro_api
_default_client: TushareClient | None = None


def get_client() -> TushareClient:
    global _default_client
    if _default_client is None:
        _default_client = TushareClient()
    return _default_client
