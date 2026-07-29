"""数据服务：数据概览、校验结果、同步日志。

聚合一律在 DuckDB 侧完成，不把千万级行拉进 Python。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from quant_data import storage

_FACTS = [("daily_quote", "ts_code", "trade_date"),
          ("daily_basic", "ts_code", "trade_date"),
          ("financial", "ts_code", "end_date"),
          ("index_daily", "index_code", "trade_date")]

_DIMS = ["trade_calendar", "stock_basic", "namechange",
         "index_member", "industry_member", "risk_free_rate"]


def overview() -> dict:
    dims = []
    for n in _DIMS:
        d = storage.read_dim(n)
        dims.append({"name": n, "rows": int(len(d))})

    facts = []
    for t, ent, dcol in _FACTS:
        src = storage.fact_source(t)
        if not src:
            facts.append({"name": t, "rows": 0})
            continue
        r = storage.query(f"""SELECT count(*) n, count(DISTINCT {ent}) ent,
                count(DISTINCT {dcol}) dts, min({dcol}) mn, max({dcol}) mx
            FROM {src}""").iloc[0]
        facts.append({"name": t, "rows": int(r["n"]), "entities": int(r["ent"]),
                      "dates": int(r["dts"]), "start": str(r["mn"]), "end": str(r["mx"])})

    sb = storage.read_dim("stock_basic")
    status = sb["list_status"].value_counts().to_dict() if not sb.empty else {}

    # 逐年不同股票数（一眼看出数据缺口）
    src = storage.fact_source("daily_quote")
    yearly = []
    if src:
        y = storage.query(f"""SELECT substr(trade_date,1,4) y, count(DISTINCT ts_code) stocks,
                round(count(*)*1.0/count(DISTINCT trade_date),0) avg_per_day
            FROM {src} GROUP BY 1 ORDER BY 1""")
        yearly = [{"year": r["y"], "stocks": int(r["stocks"]),
                   "avg_per_day": int(r["avg_per_day"])} for _, r in y.iterrows()]

    return {"dims": dims, "facts": facts,
            "stock_status": {k: int(v) for k, v in status.items()},
            "yearly": yearly}


def quality(limit: int = 500) -> list[dict]:
    df = storage.read_meta("check_result")
    if df.empty:
        return []
    df = df.sort_values(["passed", "check_time"], ascending=[True, False]).head(limit)
    df = df.replace({np.nan: None})
    if "check_time" in df.columns:
        df["check_time"] = df["check_time"].astype(str)
    return df.to_dict("records")


def sync_log(limit: int = 500) -> dict:
    df = storage.read_meta("sync_log")
    if df.empty:
        return {"summary": [], "failures": []}
    g = df.groupby(["task_name", "status"]).size().reset_index(name="n")
    summary = g.to_dict("records")
    bad = df[df["status"] != "SUCCESS"].head(limit).copy()
    for c in ("start_time", "end_time"):
        if c in bad.columns:
            bad[c] = bad[c].astype(str)
    return {"summary": [{k: (int(v) if k == "n" else v) for k, v in r.items()} for r in summary],
            "failures": bad.replace({np.nan: None}).to_dict("records")}


def list_industries() -> list[dict]:
    """SW 一级行业清单（供股票池行业筛选），按名称排序。"""
    im = storage.read_dim("industry_member")
    if im.empty or "industry_name" not in im.columns:
        return []
    sub = (im[(im["src"] == config.SW_INDUSTRY_SRC) & (im["level"] == "L1")]
           [["industry_code", "industry_name"]].drop_duplicates().sort_values("industry_name"))
    return [{"code": r.industry_code, "name": r.industry_name} for r in sub.itertuples()]
