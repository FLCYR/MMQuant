"""每日估值与市值 fact_daily_basic（Tushare daily_basic）。

市值/估值必须是日频，不能用最新值回溯历史——规模因子与市值中性化直接依赖它。
按 trade_date 一次拉全市场。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import config
from quant_data.client import TushareClient, get_client
from quant_data import storage, synclog
from quant_data.checks import base, rules_basic
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.daily_basic")

TASK = "daily_basic"
KEYS = ["ts_code", "trade_date"]
FIELDS = ("ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,"
          "pe,pe_ttm,pb,ps_ttm,dv_ttm,total_share,float_share,free_share,"
          "total_mv,circ_mv")


def fetch_day(client: TushareClient, trade_date: str) -> pd.DataFrame:
    df = client.call("daily_basic", trade_date=str(trade_date), fields=FIELDS)
    if df.empty:
        return df
    df["trade_date"] = df["trade_date"].astype(str)
    df["update_time"] = datetime.now()
    return df


def sync_day(client: TushareClient, trade_date: str, validate: bool = True) -> int:
    trade_date = str(trade_date)
    start = datetime.now()
    df = fetch_day(client, trade_date)
    if df.empty:
        synclog.record(TASK, trade_date, "FAILED", 0, error_msg="拉取为空", start_time=start)
        return 0

    reject: set[str] = set()
    if validate:
        results = base.run_checks(df, rules_basic.RULES, "fact_daily_basic", trade_date)
        base.persist(results)
        base.log_failures(results)
        for r in results:
            if not r.passed and r.level == "FATAL" and r.fail_keys:
                reject |= set(r.fail_keys)

    main = df[~df["ts_code"].isin(reject)] if reject else df
    if reject:
        storage.write_isolation(TASK, trade_date, df[df["ts_code"].isin(reject)])
    n = storage.write_fact("daily_basic", main, part_col="trade_date", keys=KEYS)
    synclog.record(TASK, trade_date, "PARTIAL" if reject else "SUCCESS", n, start_time=start)
    return n


def sync_dates(dates: list[str], client: TushareClient | None = None,
               validate: bool = True) -> int:
    client = client or get_client()
    total = 0
    for i, d in enumerate(dates, 1):
        try:
            total += sync_day(client, d, validate)
            if i % 20 == 0 or i == len(dates):
                log.info("进度 %d/%d，累计 %d 行", i, len(dates), total)
        except Exception as e:
            synclog.record(TASK, d, "FAILED", 0, error_msg=str(e))
            log.error("%s 同步失败：%s", d, e)
    return total


if __name__ == "__main__":
    from quant_data import calendar
    ds = calendar.get_trade_dates(config.START_DATE, "20150131")
    sync_dates(ds)
