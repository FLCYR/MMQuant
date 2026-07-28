"""ST 标记推导（Tushare namechange）。

股票名称含 "ST"（含 *ST/SST 等变体）的区间标记为 ST。namechange 给出每次
更名的生效区间 [start_date, end_date]，end_date 为空表示当前仍用该名。
"""
from __future__ import annotations

import pandas as pd

from quant_data.client import TushareClient, get_client
from quant_data import storage
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.st_flag")

FAR = "99991231"                 # 区间未结束时的哨兵结束日
FIELDS = "ts_code,name,start_date,end_date,ann_date,change_reason"

_intervals_cache: pd.DataFrame | None = None


def sync(client: TushareClient | None = None) -> int:
    client = client or get_client()
    df = client.call("namechange", fields=FIELDS, paged=True)
    if df.empty:
        log.warning("namechange 返回空")
        return 0
    df["start_date"] = df["start_date"].astype("string")
    df["end_date"] = df["end_date"].astype("string")
    df = df.drop_duplicates(["ts_code", "start_date", "name"]).sort_values(
        ["ts_code", "start_date"]).reset_index(drop=True)
    storage.write_dim("namechange", df)
    global _intervals_cache
    _intervals_cache = None       # 失效缓存
    st_cnt = int(df["name"].astype(str).str.contains("ST", na=False).sum())
    log.info("namechange 同步完成：%d 行（含 ST 名称 %d 条）", len(df), st_cnt)
    return len(df)


def st_intervals(reload: bool = False) -> pd.DataFrame:
    """返回仅含 ST 名称的区间表：ts_code, start_date, end_fill。"""
    global _intervals_cache
    if _intervals_cache is None or reload:
        df = storage.read_dim("namechange")
        if df.empty:
            _intervals_cache = pd.DataFrame(columns=["ts_code", "start_date", "end_fill"])
        else:
            st = df[df["name"].astype(str).str.contains("ST", na=False)].copy()
            st["end_fill"] = st["end_date"].fillna(FAR)
            _intervals_cache = st[["ts_code", "start_date", "end_fill"]].reset_index(drop=True)
    return _intervals_cache


def st_codes_on(date: str, intervals: pd.DataFrame | None = None) -> set[str]:
    """某交易日处于 ST 状态的 ts_code 集合。"""
    st = intervals if intervals is not None else st_intervals()
    if st.empty:
        return set()
    d = str(date)
    m = (st["start_date"] <= d) & (st["end_fill"] >= d)
    return set(st.loc[m, "ts_code"])


if __name__ == "__main__":
    sync()
