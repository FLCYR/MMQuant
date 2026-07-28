"""因子基类、注册表与取数辅助。

每个因子是一个 compute(dates)->DataFrame[trade_date, ts_code, value] 的无状态函数，
经 @register 注册。价格/量因子用 DuckDB 窗口在全历史算好再取调仓日；财务因子走 PIT。
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from quant_data import storage, pit

REGISTRY: dict[str, "Factor"] = {}


@dataclass
class Factor:
    name: str
    style: str
    direction: int          # 预期 IC 方向：+1 越大越好，-1 越小越好
    fn: Callable[[list[str]], pd.DataFrame]


def register(name: str, style: str, direction: int):
    def deco(fn):
        REGISTRY[name] = Factor(name, style, direction, fn)
        return fn
    return deco


# --------------------------------------------------------------- 取数辅助
def DQ() -> str:
    return storage.fact_source("daily_quote")


def DB() -> str:
    return storage.fact_source("daily_basic")


def _dstr(dates: list[str]) -> str:
    return ",".join(f"'{d}'" for d in dates)


def query_at_dates(inner_sql: str, dates: list[str]) -> pd.DataFrame:
    """inner_sql 需产出 (ts_code, trade_date, value)（全历史）；本函数取指定调仓日、去空。"""
    if not dates:
        return pd.DataFrame(columns=["trade_date", "ts_code", "value"])
    df = storage.query(f"""
        SELECT trade_date, ts_code, value FROM ({inner_sql}) t
        WHERE trade_date IN ({_dstr(dates)}) AND value IS NOT NULL
          AND isfinite(value)
    """)
    return df


# --------------------------------------------------------------- 财务 PIT（缓存）
@functools.lru_cache(maxsize=1200)
def _pit_slim(T: str) -> pd.DataFrame:
    p = pit.get_financial_pit(T)
    cols = ["ts_code", "roe", "grossprofit_margin", "netprofit_yoy", "or_yoy"]
    return p[cols].copy() if not p.empty else pd.DataFrame(columns=cols)


def financial_factor(dates: list[str], col: str) -> pd.DataFrame:
    """按调仓日取 PIT 财务字段 col 作为因子值。"""
    frames = []
    for T in dates:
        p = _pit_slim(str(T))
        if p.empty:
            continue
        s = p[["ts_code", col]].dropna()
        s = s.rename(columns={col: "value"})
        s["trade_date"] = str(T)
        frames.append(s[["trade_date", "ts_code", "value"]])
    if not frames:
        return pd.DataFrame(columns=["trade_date", "ts_code", "value"])
    return pd.concat(frames, ignore_index=True)
