"""绩效指标：绝对收益、风险、相对基准的超额与信息比率。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
from quant_data import storage

PPY = config.TRADING_DAYS_PER_YEAR


# --------------------------------------------------------------- 基础
def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.astype(float).pct_change().dropna()


def annualized_return(nav: pd.Series, ppy: int = PPY) -> float:
    if len(nav) < 2 or nav.iloc[0] <= 0:
        return float("nan")
    years = len(nav) / ppy
    return float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1)


def annualized_vol(rets: pd.Series, ppy: int = PPY) -> float:
    return float(rets.std(ddof=1) * np.sqrt(ppy)) if len(rets) > 1 else float("nan")


def max_drawdown(nav: pd.Series) -> float:
    peak = nav.cummax()
    return float((nav / peak - 1.0).min())


def sharpe(rets: pd.Series, rf_annual: float, ppy: int = PPY) -> float:
    if len(rets) < 2 or rets.std(ddof=1) == 0:
        return float("nan")
    ex = rets - rf_annual / ppy
    return float(ex.mean() / rets.std(ddof=1) * np.sqrt(ppy))


# --------------------------------------------------------------- 外部数据
def load_rf(dates: list[str]) -> float:
    """区间平均年化无风险利率（小数）。"""
    rf = storage.read_dim("risk_free_rate")
    if rf.empty:
        return 0.025
    s = rf[rf["trade_date"].astype(str).isin(set(map(str, dates)))]["rate_1y"]
    return float(pd.to_numeric(s, errors="coerce").mean() / 100.0) if len(s) else 0.025


def benchmark_nav(dates: list[str], index_code: str = config.CSI500) -> pd.Series:
    src = storage.fact_source("index_daily")
    if not src:
        return pd.Series(dtype=float)
    dstr = ",".join(f"'{d}'" for d in dates)
    df = storage.query(f"""SELECT CAST(trade_date AS VARCHAR) trade_date, close
        FROM {src} WHERE index_code='{index_code}' AND trade_date IN ({dstr})""")
    if df.empty:
        return pd.Series(dtype=float)
    s = df.set_index("trade_date")["close"].sort_index().astype(float)
    return s / s.iloc[0]


# --------------------------------------------------------------- 汇总
@dataclass
class Performance:
    label: str
    total_return: float
    ann_return: float
    ann_vol: float
    sharpe: float
    max_dd: float
    calmar: float
    win_rate: float
    excess_ann: float = float("nan")
    tracking_error: float = float("nan")
    info_ratio: float = float("nan")
    turnover_ann: float = float("nan")
    total_cost_pct: float = float("nan")
    yearly: dict = field(default_factory=dict)


def summarize(nav: pd.Series, label: str, bench: pd.Series | None = None,
              rf_annual: float = 0.025, turnover: pd.Series | None = None,
              total_cost: float = 0.0, init_cash: float = 1.0) -> Performance:
    nav = nav.astype(float)
    rets = daily_returns(nav)
    ann = annualized_return(nav)
    mdd = max_drawdown(nav)

    perf = Performance(
        label=label,
        total_return=float(nav.iloc[-1] / nav.iloc[0] - 1),
        ann_return=ann,
        ann_vol=annualized_vol(rets),
        sharpe=sharpe(rets, rf_annual),
        max_dd=mdd,
        calmar=float(ann / abs(mdd)) if mdd < 0 else float("nan"),
        win_rate=float((rets > 0).mean()) if len(rets) else float("nan"),
    )

    if bench is not None and len(bench) > 1:
        b = bench.reindex(nav.index).ffill()
        brets = daily_returns(b)
        idx = rets.index.intersection(brets.index)
        diff = rets.loc[idx] - brets.loc[idx]
        perf.excess_ann = ann - annualized_return(b)
        perf.tracking_error = annualized_vol(diff)
        perf.info_ratio = (float(diff.mean() / diff.std(ddof=1) * np.sqrt(PPY))
                           if diff.std(ddof=1) else float("nan"))

    if turnover is not None and len(turnover):
        years = len(nav) / PPY
        perf.turnover_ann = float(turnover.sum() / years)
    perf.total_cost_pct = float(total_cost / init_cash) if init_cash else float("nan")

    yr = {}
    tmp = pd.DataFrame({"nav": nav})
    tmp["year"] = [str(i)[:4] for i in tmp.index]
    for y, g in tmp.groupby("year"):
        if len(g) > 1:
            yr[y] = float(g["nav"].iloc[-1] / g["nav"].iloc[0] - 1)
    perf.yearly = yr
    return perf
