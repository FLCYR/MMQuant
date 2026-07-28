"""数据服务：数据概览、校验结果、同步日志、个股 K 线。

聚合一律在 DuckDB 侧完成，不把千万级行拉进 Python。
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

import config
from quant_data import calendar, storage

_DATE_RE = re.compile(r"^\d{8}$")


def _validate_date(d: str, field: str = "日期") -> str:
    """校验 YYYYMMDD 格式；HTTP 参数在拼入/传入 SQL 前必须过这一关，防注入。"""
    d = str(d)
    if not _DATE_RE.match(d):
        raise ValueError(f"{field}格式非法（应为 YYYYMMDD）：{d!r}")
    return d


def _validate_ts_code(ts_code: str) -> str:
    """校验 ts_code 确实存在于 stock_basic——比正则白名单更严格（真实存在性校验），
    杜绝任何拼接注入。"""
    sb = storage.read_dim("stock_basic")
    if sb.empty or ts_code not in set(sb["ts_code"]):
        raise ValueError(f"未知股票代码：{ts_code!r}")
    return ts_code

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


def kline(ts_code: str, start: str, end: str, limit: int = 1200) -> dict:
    """个股 K 线（原始价，标注涨跌停/停牌），供前端画蜡烛图。"""
    ts_code = _validate_ts_code(ts_code)
    start = _validate_date(start, "起始日")
    end = _validate_date(end, "结束日")
    src = storage.fact_source("daily_quote")
    if not src:
        return {"rows": []}
    df = storage.query(f"""
        SELECT CAST(trade_date AS VARCHAR) trade_date, open, high, low, close, vol, amount,
               adj_factor, limit_up, limit_down, is_suspended, is_st
        FROM {src}
        WHERE ts_code=? AND trade_date>=? AND trade_date<=?
        ORDER BY trade_date
    """, [ts_code, start, end])
    if df.empty:
        return {"rows": []}
    if len(df) > limit:
        df = df.tail(limit)
    df = df.replace({np.nan: None})
    return {"ts_code": ts_code, "rows": df.to_dict("records")}


def search_stocks(q: str, limit: int = 20) -> list[dict]:
    sb = storage.read_dim("stock_basic")
    if sb.empty or not q:
        return []
    m = sb[sb["ts_code"].str.contains(q, case=False, na=False) |
           sb["name"].astype(str).str.contains(q, case=False, na=False)]
    cols = [c for c in ["ts_code", "name", "market", "list_date", "list_status"] if c in m.columns]
    return m.head(limit)[cols].replace({np.nan: None}).to_dict("records")


def trade_dates(start: str, end: str) -> list[str]:
    return calendar.get_trade_dates(start, end)


def list_industries() -> list[dict]:
    """SW 一级行业清单（供股票池行业筛选），按名称排序。"""
    im = storage.read_dim("industry_member")
    if im.empty or "industry_name" not in im.columns:
        return []
    sub = (im[(im["src"] == config.SW_INDUSTRY_SRC) & (im["level"] == "L1")]
           [["industry_code", "industry_name"]].drop_duplicates().sort_values("industry_name"))
    return [{"code": r.industry_code, "name": r.industry_name} for r in sub.itertuples()]
