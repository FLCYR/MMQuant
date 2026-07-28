"""财务数据 fact_financial（PIT 设计，最易出错）。

逐股拉取 income / balancesheet / cashflow / fina_indicator，按
(ts_code, end_date, ann_date, report_type) 外连接合并成宽表。

**保留每个报告期的所有 ann_date 版本**（财报会被修正，同一报告期有多版本）。
绝不在入库时按 ann_date 去重——那等于用了当时不存在的数据。去重只在查询时
按 "ann_date <= T 的最新版本" 进行（见 pit.get_financial_pit）。

按 end_date 年份分区写入；逐股拉取但按批累积后一次性落盘，避免频繁重写分区。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import config
from quant_data.client import TushareClient, get_client
from quant_data import storage, synclog
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.financial")

TASK = "financial"
KEYS = ["ts_code", "end_date", "ann_date", "report_type"]
MIN_END_DATE = "20140101"        # 报告期下限（留一年做同比基数冗余）

INCOME_F = "ts_code,end_date,ann_date,report_type,revenue,n_income_attr_p"
BS_F = "ts_code,end_date,ann_date,report_type,total_assets,total_hldr_eqy_inc_min_int"
CF_F = "ts_code,end_date,ann_date,report_type,n_cashflow_act"
FI_F = ("ts_code,end_date,ann_date,roe,roa,grossprofit_margin,netprofit_margin,"
        "debt_to_assets,or_yoy,netprofit_yoy,update_flag")

OUT_COLS = ["ts_code", "end_date", "ann_date", "report_type",
            "revenue", "n_income_attr_p", "total_assets", "total_equity",
            "n_cashflow_act", "roe", "roa", "grossprofit_margin",
            "netprofit_margin", "debt_to_assets", "or_yoy", "netprofit_yoy",
            "update_flag", "update_time"]


def _norm(df: pd.DataFrame, consolidated_only: bool) -> pd.DataFrame:
    if df.empty:
        return df
    for c in ("end_date", "ann_date"):
        if c in df.columns:
            df[c] = df[c].astype("string")
    if "report_type" in df.columns:
        df["report_type"] = df["report_type"].astype("string")
        if consolidated_only:
            df = df[df["report_type"] == "1"]
    return df


def fetch_stock(client: TushareClient, ts_code: str) -> pd.DataFrame:
    inc = _norm(client.call("income", ts_code=ts_code, fields=INCOME_F), True)
    bs = _norm(client.call("balancesheet", ts_code=ts_code, fields=BS_F), True)
    cf = _norm(client.call("cashflow", ts_code=ts_code, fields=CF_F), True)
    fi = _norm(client.call("fina_indicator", ts_code=ts_code, fields=FI_F), False)

    if inc.empty and bs.empty and cf.empty and fi.empty:
        return pd.DataFrame(columns=OUT_COLS)

    m = inc
    for other in (bs, cf):
        m = other if m.empty else (m if other.empty else m.merge(other, on=KEYS, how="outer"))
    # fina_indicator 无 report_type，视为合并报表
    if not fi.empty:
        fi = fi.copy()
        fi["report_type"] = "1"
        m = fi if m.empty else m.merge(fi, on=["ts_code", "end_date", "ann_date", "report_type"],
                                       how="outer")

    m = m.rename(columns={"total_hldr_eqy_inc_min_int": "total_equity"})
    # 保证所有列存在
    for col in OUT_COLS:
        if col not in m.columns:
            m[col] = pd.NA
    m = m[m["end_date"].notna() & m["ann_date"].notna()]
    m = m[m["end_date"] >= MIN_END_DATE]
    # 同一 (ts_code,end_date,ann_date,report_type) 若重复，保留 update_flag 较大的
    m = m.sort_values(KEYS + ["update_flag"]).drop_duplicates(KEYS, keep="last")
    m["update_time"] = datetime.now()
    return m[OUT_COLS].reset_index(drop=True)


def sync_stocks(codes: list[str], client: TushareClient | None = None,
                batch_write: int = 300) -> int:
    client = client or get_client()
    buf: list[pd.DataFrame] = []
    total = 0

    def flush():
        nonlocal buf, total
        if not buf:
            return
        batch = pd.concat(buf, ignore_index=True)
        total += storage.write_fact("financial", batch, part_col="end_date", keys=KEYS)
        buf = []

    for i, code in enumerate(codes, 1):
        try:
            df = fetch_stock(client, code)
            if not df.empty:
                buf.append(df)
            synclog.record(TASK, code, "SUCCESS", len(df))
        except Exception as e:
            synclog.record(TASK, code, "FAILED", 0, error_msg=str(e))
            log.error("%s 财务同步失败：%s", code, e)
        if i % batch_write == 0:
            flush()
            log.info("财务进度 %d/%d，累计写入 %d 行", i, len(codes), total)
    flush()
    log.info("财务同步完成：%d 只股票，累计 %d 行", len(codes), total)
    return total


if __name__ == "__main__":
    from quant_data import storage as _s
    sb = _s.read_dim("stock_basic")
    sync_stocks(sb["ts_code"].head(10).tolist())
