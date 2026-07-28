"""股票基本信息 dim_stock_basic（Tushare stock_basic）。

必须包含已退市（D）与暂停上市（P）股票——只拉在市股票是幸存者偏差最常见来源。
stock_basic 默认只返回 list_status='L'，需按状态分别拉取后合并。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant_data.client import TushareClient, get_client
from quant_data import storage
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.stock_basic")

STATUSES = ("L", "D", "P")       # 上市 / 退市 / 暂停上市
FIELDS = ("ts_code,symbol,name,area,market,exchange,"
          "list_date,delist_date,list_status")


def sync(client: TushareClient | None = None) -> int:
    client = client or get_client()
    frames = []
    for st in STATUSES:
        df = client.call("stock_basic", list_status=st, fields=FIELDS, paged=True)
        if not df.empty:
            df["list_status"] = st
            frames.append(df)
        log.info("stock_basic list_status=%s：%d 行", st, len(df))
    out = pd.concat(frames, ignore_index=True)

    # 规整
    for col in ("list_date", "delist_date"):
        out[col] = out[col].astype("string")
    out["update_time"] = datetime.now()
    out = out.drop_duplicates("ts_code").sort_values("ts_code").reset_index(drop=True)

    n = storage.write_dim("stock_basic", out)
    n_delist = int((out["list_status"] == "D").sum())
    log.info("股票基本信息同步完成：%d 只（其中退市 %d 只）", n, n_delist)
    return n


if __name__ == "__main__":
    sync()
