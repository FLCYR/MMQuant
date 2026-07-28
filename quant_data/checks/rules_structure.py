"""C1xx 结构层通用规则（工厂函数，按表配置主键/非空列）。"""
from __future__ import annotations

import pandas as pd

from quant_data.checks.base import CheckResult, _sample


def non_empty():
    """C104 单次拉取返回行数 > 0（批级 FATAL）。"""
    def _c(df: pd.DataFrame) -> CheckResult:
        n = len(df)
        return CheckResult("C104", "FATAL", n > 0, 0 if n > 0 else 1,
                           "" if n > 0 else "空数据集")
    _c.check_id = "C104"
    return _c


def pk_unique(keys: list[str]):
    """C102 主键无重复（行级 FATAL，违规行进隔离区）。"""
    def _c(df: pd.DataFrame) -> CheckResult:
        dup = df.duplicated(keys, keep=False)
        n = int(dup.sum())
        fk = df.loc[dup, keys[0]].tolist() if n else []
        return CheckResult("C102", "FATAL", n == 0, n,
                           _sample(df.loc[dup], keys), fail_keys=fk)
    _c.check_id = "C102"
    return _c


def not_null(cols: list[str], key_col: str = "ts_code"):
    """C103 NOT NULL 字段无空值（行级 FATAL）。"""
    def _c(df: pd.DataFrame) -> CheckResult:
        bad = df[cols].isna().any(axis=1)
        n = int(bad.sum())
        fk = df.loc[bad, key_col].tolist() if n else []
        return CheckResult("C103", "FATAL", n == 0, n,
                           _sample(df.loc[bad], cols), fail_keys=fk)
    _c.check_id = "C103"
    return _c
