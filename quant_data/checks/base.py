"""校验框架：CheckResult、run_checks、结果落库。

分级处置（见文档 §5.1）在采集器里执行，本模块只负责运行规则与记录结果。

约定：
- 规则函数签名 rule(df) -> CheckResult，无状态、单一职责。
- 行级规则把违规主键放进 CheckResult.fail_keys（ts_code 列表），供采集器按行隔离。
- 批级规则（如"行数>0"）fail_keys 留 None，表示整批处置。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from quant_data import storage
from quant_data.log import get_logger

log = get_logger("quant_data.checks")

_LEVEL_ORDER = {"WARN": 0, "ERROR": 1, "FATAL": 2}


@dataclass
class CheckResult:
    check_id: str
    level: str                    # FATAL / ERROR / WARN
    passed: bool
    fail_count: int
    sample: str = ""
    table_name: str = ""
    biz_date: str = ""
    fail_keys: list | None = None  # 行级违规 ts_code；None=批级


def _sample(df: pd.DataFrame, cols: list[str] | None = None, n: int = 5) -> str:
    try:
        sub = df[cols] if cols else df
        return str(sub.head(n).to_dict("records"))[:1000]
    except Exception:
        return ""


def run_checks(df: pd.DataFrame, rules, table_name: str, biz_date: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    for rule in rules:
        try:
            r = rule(df)
        except Exception as e:  # 单条规则异常不影响其他规则
            r = CheckResult(getattr(rule, "check_id", rule.__name__), "FATAL",
                            False, -1, f"规则执行异常: {e}")
        r.table_name = r.table_name or table_name
        r.biz_date = r.biz_date or biz_date
        results.append(r)
    return results


def worst_level(results: list[CheckResult]) -> str | None:
    lvl = None
    for r in results:
        if not r.passed and (lvl is None or _LEVEL_ORDER[r.level] > _LEVEL_ORDER[lvl]):
            lvl = r.level
    return lvl


def persist(results: list[CheckResult], check_time: datetime | None = None) -> None:
    if not results:
        return
    now = check_time or datetime.now()
    rows = [{
        "check_id": r.check_id,
        "table_name": r.table_name,
        "biz_date": str(r.biz_date),
        "level": r.level,
        "passed": int(r.passed),
        "fail_count": int(r.fail_count),
        "sample": r.sample,
        "check_time": now,
    } for r in results]
    storage.write_meta("check_result", pd.DataFrame(rows),
                       ["check_id", "table_name", "biz_date"])


def log_failures(results: list[CheckResult]) -> None:
    for r in results:
        if not r.passed:
            log.warning("[%s] %s %s 失败 %d 条：%s",
                        r.level, r.check_id, r.table_name, r.fail_count, r.sample)
