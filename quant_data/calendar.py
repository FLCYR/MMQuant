"""交易日历工具。一切日期序列以此为准，禁止用自然日或 bdate_range。"""
from __future__ import annotations

import config
from quant_data import storage


def load_calendar(exchange: str = config.DEFAULT_EXCHANGE):
    df = storage.read_dim("trade_calendar")
    if df.empty:
        raise RuntimeError("交易日历为空，请先运行 P1 拉取 trade_cal")
    return df[df["exchange"] == exchange]


def get_trade_dates(start: str | None = None, end: str | None = None,
                    exchange: str = config.DEFAULT_EXCHANGE) -> list[str]:
    df = load_calendar(exchange)
    days = df.loc[df["is_open"] == 1, "cal_date"].astype(str)
    s = str(start) if start else "00000000"
    e = str(end) if end else "99999999"
    sel = days[(days >= s) & (days <= e)]
    return sorted(sel.tolist())


def get_pretrade_date(date: str, exchange: str = config.DEFAULT_EXCHANGE) -> str | None:
    df = load_calendar(exchange)
    row = df[df["cal_date"].astype(str) == str(date)]
    if row.empty:
        return None
    val = row.iloc[0]["pretrade_date"]
    return str(val) if val is not None else None


def is_trade_date(date: str, exchange: str = config.DEFAULT_EXCHANGE) -> bool:
    df = load_calendar(exchange)
    row = df[(df["cal_date"].astype(str) == str(date))]
    return (not row.empty) and int(row.iloc[0]["is_open"]) == 1
