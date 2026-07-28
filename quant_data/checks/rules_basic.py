"""fact_daily_basic 的取值规则（C305 / C306 / C307）。

估值字段常有正常缺失（如无 PE），故每条规则只在相关列非空的行上判定。
"""
from __future__ import annotations

from quant_data.checks.base import CheckResult, _sample
from quant_data.checks.rules_structure import non_empty, pk_unique, not_null


def c305_pb_positive(df):
    sub = df[df["pb"].notna()]
    bad = sub[sub["pb"] <= 0]
    return CheckResult("C305", "WARN", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "pb"]))


def c306_turnover_range(df):
    sub = df[df["turnover_rate"].notna()]
    bad = sub[(sub["turnover_rate"] < 0) | (sub["turnover_rate"] > 100)]
    return CheckResult("C306", "WARN", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "turnover_rate"]))


def c307_mv_relation(df):
    sub = df[df["total_mv"].notna() & df["circ_mv"].notna()]
    bad = sub[~((sub["total_mv"] >= sub["circ_mv"]) & (sub["circ_mv"] > 0))]
    return CheckResult("C307", "ERROR", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "total_mv", "circ_mv"]))


RULES = [
    non_empty(),
    pk_unique(["ts_code", "trade_date"]),
    not_null(["ts_code", "trade_date"]),
    c305_pb_positive,
    c306_turnover_range,
    c307_mv_relation,
]
