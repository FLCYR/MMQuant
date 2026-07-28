"""指数日线 fact_index_daily（Tushare index_daily）。

用于超额收益、IR、市场基准比较。按 index_code 逐个拉取全区间。
"""
from __future__ import annotations

import pandas as pd

import config
from quant_data.client import TushareClient, get_client
from quant_data import storage, synclog
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.index_daily")

TASK = "index_daily"
KEYS = ["index_code", "trade_date"]
FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount"


def sync(client: TushareClient | None = None,
         indices: list[str] | None = None,
         start: str = config.START_DATE, end: str | None = None) -> int:
    client = client or get_client()
    indices = indices or config.BENCHMARK_INDICES
    total = 0
    for code in indices:
        df = client.call("index_daily", ts_code=code, start_date=start,
                         end_date=end or "", fields=FIELDS, paged=True)
        if df.empty:
            synclog.record(TASK, code, "FAILED", 0, error_msg="拉取为空")
            log.warning("指数 %s 无数据", code)
            continue
        df = df.rename(columns={"ts_code": "index_code"})
        df["trade_date"] = df["trade_date"].astype(str)
        n = storage.write_fact("index_daily", df, part_col="trade_date", keys=KEYS)
        synclog.record(TASK, code, "SUCCESS", n)
        total += n
        log.info("指数 %s 日线：%d 行", code, n)
    return total


if __name__ == "__main__":
    sync()
