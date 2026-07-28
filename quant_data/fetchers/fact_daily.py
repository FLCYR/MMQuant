"""日线行情 fact_daily_quote（核心表）。

合并策略（见探测结论）：以 adj_factor 为主键骨架（保证复权因子非空），左连
daily（原始 OHLCV）与 stk_limit（涨跌停价）。is_suspended = 有复权因子但当日
无成交。**只存原始价 + adj_factor，不存复权价**（复权价使用时再算）。

校验分级处置（务实版）：
- 结构类 FATAL（C104 空 / C102 主键重复 / C103 关键列空 / C301/C302/C308 取值非法）
  → 违规行进隔离区、其余行照常入主表；整批为空才判 FAILED。
- ERROR / WARN → 仅记录到 meta_check_result（复牌大波动等合理场景会命中 ERROR，
  不据此丢弃全市场当日数据）。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import config
from quant_data.client import TushareClient, get_client
from quant_data import storage, synclog
from quant_data.fetchers import st_flag
from quant_data.checks import base, rules_quote
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.daily_quote")

TASK = "daily_quote"
KEYS = ["ts_code", "trade_date"]
COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
        "change", "pct_chg", "vol", "amount", "adj_factor",
        "limit_up", "limit_down", "is_suspended", "is_st", "update_time"]
_PRICE = ["open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]


def fetch_day(client: TushareClient, trade_date: str,
              st_intervals: pd.DataFrame | None = None) -> pd.DataFrame:
    trade_date = str(trade_date)
    adj = client.call("adj_factor", trade_date=trade_date,
                      fields="ts_code,adj_factor")
    if adj.empty:
        return pd.DataFrame()      # 非交易日或数据未出

    daily = client.call("daily", trade_date=trade_date,
                        fields="ts_code,open,high,low,close,pre_close,change,pct_chg,vol,amount")
    lim = client.call("stk_limit", trade_date=trade_date,
                      fields="ts_code,up_limit,down_limit")

    df = adj[["ts_code", "adj_factor"]].drop_duplicates("ts_code").copy()
    df["trade_date"] = trade_date

    if not daily.empty:
        traded = set(daily["ts_code"])
        df = df.merge(daily[["ts_code"] + _PRICE], on="ts_code", how="left")
    else:                                   # 全天无成交（极端情形）
        traded = set()
        for c in _PRICE:
            df[c] = pd.NA

    if not lim.empty:
        lim = lim.rename(columns={"up_limit": "limit_up", "down_limit": "limit_down"})
        df = df.merge(lim[["ts_code", "limit_up", "limit_down"]], on="ts_code", how="left")
    else:
        df["limit_up"] = pd.NA
        df["limit_down"] = pd.NA

    df["is_suspended"] = (~df["ts_code"].isin(traded)).astype("int8")
    # 停牌行成交量/额记 0，价格留空
    df.loc[df["is_suspended"] == 1, ["vol", "amount"]] = 0

    st_codes = st_flag.st_codes_on(trade_date, st_intervals)
    df["is_st"] = df["ts_code"].isin(st_codes).astype("int8")
    df["update_time"] = datetime.now()
    return df[COLS]


def sync_day(client: TushareClient, trade_date: str,
             st_intervals: pd.DataFrame | None = None,
             validate: bool = True) -> tuple[int, list[base.CheckResult]]:
    trade_date = str(trade_date)
    start = datetime.now()
    df = fetch_day(client, trade_date, st_intervals)
    if df.empty:
        synclog.record(TASK, trade_date, "FAILED", 0,
                       error_msg="拉取为空", start_time=start)
        log.warning("%s 拉取为空，标记 FAILED", trade_date)
        return 0, []

    results: list[base.CheckResult] = []
    reject: set[str] = set()
    if validate:
        results = base.run_checks(df, rules_quote.RULES, "fact_daily_quote", trade_date)
        base.persist(results)
        base.log_failures(results)
        for r in results:
            if not r.passed and r.level == "FATAL" and r.fail_keys:
                reject |= set(r.fail_keys)

    main = df[~df["ts_code"].isin(reject)] if reject else df
    if reject:
        storage.write_isolation(TASK, trade_date, df[df["ts_code"].isin(reject)])

    n = storage.write_fact("daily_quote", main, part_col="trade_date", keys=KEYS)
    status = "PARTIAL" if reject else "SUCCESS"
    synclog.record(TASK, trade_date, status, n,
                   error_msg=f"隔离 {len(reject)} 行" if reject else "",
                   start_time=start)
    return n, results


def sync_dates(dates: list[str], client: TushareClient | None = None,
               validate: bool = True) -> int:
    client = client or get_client()
    intervals = st_flag.st_intervals()      # 预取一次 ST 区间，供全批复用
    total = 0
    for i, d in enumerate(dates, 1):
        try:
            n, _ = sync_day(client, d, intervals, validate)
            total += n
            if i % 20 == 0 or i == len(dates):
                log.info("进度 %d/%d，累计写入 %d 行", i, len(dates), total)
        except Exception as e:
            synclog.record(TASK, d, "FAILED", 0, error_msg=str(e))
            log.error("%s 同步失败：%s", d, e)
    return total


if __name__ == "__main__":
    from quant_data import calendar
    ds = calendar.get_trade_dates(config.START_DATE, "20150131")
    sync_dates(ds)
