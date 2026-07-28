"""交易日历 dim_trade_calendar（Tushare trade_cal）。

一次性拉取，年度更新。范围略早于 START_DATE，以便 START_DATE 附近能查到上一交易日。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import config
from quant_data.client import TushareClient, get_client
from quant_data import storage
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.calendar")

CAL_START = "20050101"           # 日历向前多取几年，覆盖 pretrade 查询
EXCHANGES = ("SSE", "SZSE")
FIELDS = "exchange,cal_date,is_open,pretrade_date"


def sync(client: TushareClient | None = None,
         start_date: str = CAL_START,
         end_date: str | None = None) -> int:
    client = client or get_client()
    if end_date is None:
        end_date = f"{datetime.now().year + 1}1231"   # 含次年，预留调仓对齐
    frames = []
    for ex in EXCHANGES:
        df = client.call("trade_cal", exchange=ex,
                         start_date=start_date, end_date=end_date, fields=FIELDS)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out["cal_date"] = out["cal_date"].astype(str)
    out["pretrade_date"] = out["pretrade_date"].astype("string")
    out["is_open"] = out["is_open"].astype("int8")
    out = out.drop_duplicates(["exchange", "cal_date"]).sort_values(["exchange", "cal_date"])
    n = storage.write_dim("trade_calendar", out.reset_index(drop=True))
    log.info("交易日历同步完成：%d 行（%s ~ %s）", n, start_date, end_date)
    return n


if __name__ == "__main__":
    sync()
