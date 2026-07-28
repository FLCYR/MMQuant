"""fact_daily_quote 的取值/一致性规则（C3xx / C4xx，单日可判定部分）。

价格类规则只作用于**当日成交**的行（is_suspended==0）；停牌行价格为空，不参与。
跨日规则（C402/C403 复权因子单调、C601 复权探针）在 rules_anomaly 离线执行。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_data.checks.base import CheckResult, _sample
from quant_data.checks.rules_structure import non_empty, pk_unique, not_null

_PRICE = ["open", "high", "low", "close", "pre_close"]


def _traded(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["is_suspended"] == 0]


def c301_positive_price(df: pd.DataFrame) -> CheckResult:
    t = _traded(df)
    bad = t[(t[_PRICE] <= 0).any(axis=1)]
    return CheckResult("C301", "FATAL", bad.empty, len(bad),
                       _sample(bad, ["ts_code"] + _PRICE), fail_keys=bad["ts_code"].tolist())


def c302_ohlc_relation(df: pd.DataFrame) -> CheckResult:
    t = _traded(df)
    bad = t[(t["high"] < t[["open", "close"]].max(axis=1)) |
            (t["low"] > t[["open", "close"]].min(axis=1)) |
            (t["high"] < t["low"])]
    return CheckResult("C302", "FATAL", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "open", "high", "low", "close"]),
                       fail_keys=bad["ts_code"].tolist())


def c308_adj_positive(df: pd.DataFrame) -> CheckResult:
    bad = df[~(df["adj_factor"] > 0)]
    return CheckResult("C308", "FATAL", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "adj_factor"]),
                       fail_keys=bad["ts_code"].tolist())


def c303_volume_nonneg(df: pd.DataFrame) -> CheckResult:
    t = _traded(df)
    bad = t[(t["vol"] < 0) | (t["amount"] < 0)]
    return CheckResult("C303", "ERROR", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "vol", "amount"]))


def c304_pctchg_range(df: pd.DataFrame) -> CheckResult:
    """|pct_chg| 主板/创业板 <= 21；北交所放宽至 31。ERROR 仅记录不阻断
    （复牌首日等无涨跌幅限制的合理大波动会命中，故不据此丢数据）。"""
    t = _traded(df).copy()
    limit = np.where(t["ts_code"].str.endswith(".BJ"), 31.0, 21.0)
    bad = t[t["pct_chg"].abs() > limit]
    return CheckResult("C304", "ERROR", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "pct_chg"]))


def c401_close_in_limit(df: pd.DataFrame) -> CheckResult:
    """close 落在 [limit_down, limit_up]（仅涨跌停价非空的成交行）。"""
    t = _traded(df)
    t = t[t["limit_up"].notna() & t["limit_down"].notna()]
    bad = t[(t["close"] < t["limit_down"] - 1e-6) | (t["close"] > t["limit_up"] + 1e-6)]
    return CheckResult("C401", "ERROR", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "close", "limit_down", "limit_up"]))


# 采集时按顺序执行的规则集
RULES = [
    non_empty(),
    pk_unique(["ts_code", "trade_date"]),
    not_null(["ts_code", "trade_date", "adj_factor"]),
    c301_positive_price,
    c302_ohlc_relation,
    c308_adj_positive,
    c303_volume_nonneg,
    c304_pctchg_range,
    c401_close_in_limit,
]
