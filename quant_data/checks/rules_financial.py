"""C5xx 时点正确性（PIT，最重要）——作用于 fact_financial。

日期均为 'YYYYMMDD' 字符串，字典序即时间序，可直接比较。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant_data.checks.base import CheckResult, _sample
from quant_data.checks.rules_structure import non_empty


def c501_ann_notnull(df):
    bad = df[df["ann_date"].isna()]
    return CheckResult("C501", "FATAL", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "end_date"]))


def c502_ann_ge_end(df):
    sub = df[df["ann_date"].notna() & df["end_date"].notna()]
    bad = sub[sub["ann_date"] < sub["end_date"]]
    return CheckResult("C502", "FATAL", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "end_date", "ann_date"]))


def c503_ann_not_future(df):
    today = datetime.now().strftime("%Y%m%d")
    sub = df[df["ann_date"].notna()]
    bad = sub[sub["ann_date"] > today]
    return CheckResult("C503", "FATAL", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "end_date", "ann_date"]))


def c504_disclosure_delay(df):
    sub = df[df["ann_date"].notna() & df["end_date"].notna()].copy()
    ann = pd.to_datetime(sub["ann_date"], format="%Y%m%d", errors="coerce")
    end = pd.to_datetime(sub["end_date"], format="%Y%m%d", errors="coerce")
    delay = (ann - end).dt.days
    bad = sub[delay > 180]
    return CheckResult("C504", "WARN", bad.empty, len(bad),
                       _sample(bad, ["ts_code", "end_date", "ann_date"]))


RULES = [
    non_empty(),
    c501_ann_notnull,
    c502_ann_ge_end,
    c503_ann_not_future,
    c504_disclosure_delay,
]
