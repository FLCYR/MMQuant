"""区间表校验（C505 / C506 / C206）——作用于 dim_index_member / dim_industry_member。

区间重叠会导致 join 后行数膨胀、因子值静默出错，是必须每次校验的高危项。
"""
from __future__ import annotations

import pandas as pd

from quant_data.checks.base import CheckResult, _sample

FAR = "99991231"


def c506_valid_range(df: pd.DataFrame) -> CheckResult:
    """out_date > in_date 或 out_date 为空。"""
    sub = df[df["out_date"].notna()]
    bad = sub[sub["out_date"] <= sub["in_date"]]
    return CheckResult("C506", "FATAL", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "in_date", "out_date"]))


def c505_no_overlap(df: pd.DataFrame, group_keys: list[str]) -> CheckResult:
    """同一分组内区间不重叠：按 in_date 排序后，前一段 out_date <= 后一段 in_date。"""
    if df.empty:
        return CheckResult("C505", "ERROR", True, 0, "")
    d = df.copy()
    d["out_fill"] = d["out_date"].fillna(FAR)
    d = d.sort_values(group_keys + ["in_date"])
    prev_out = d.groupby(group_keys)["out_fill"].shift(1)
    same_grp = d.duplicated(group_keys, keep="first")  # 非组内首行
    overlap = same_grp & (d["in_date"] < prev_out)
    bad = d[overlap]
    return CheckResult("C505", "ERROR", bad.empty, len(bad),
                       _sample(bad, group_keys + ["in_date", "out_date"]))


def c206_member_count(df: pd.DataFrame, on_dates: list[str], nominal: int,
                      index_code: str, tol: int = 3) -> CheckResult:
    """指定日期上，成分股数量应等于指数标称数量（中证500=500，允许 ±tol）。"""
    sub = df[df["index_code"] == index_code].copy()
    sub["out_fill"] = sub["out_date"].fillna(FAR)
    bad_dates = []
    for t in on_dates:
        t = str(t)
        cnt = int(((sub["in_date"] <= t) & (sub["out_fill"] > t)).sum())
        if abs(cnt - nominal) > tol:
            bad_dates.append({"date": t, "count": cnt})
    return CheckResult("C206", "ERROR", len(bad_dates) == 0, len(bad_dates),
                       str(bad_dates[:10]))
