"""日度增量任务（建议交易日 17:30 触发）。

流程：确定目标交易日 → 合并近期未成功日期（失败回补）→ 拉取行情与估值 →
校验 → 写主表/隔离区 → 记 sync_log → C601 增量抽查。幂等，可重复执行。
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
from datetime import datetime

import config
from quant_data import calendar, synclog
from quant_data.client import get_client
from quant_data.fetchers import fact_daily, fact_daily_basic, st_flag
from quant_data.log import get_logger

log = get_logger("run_daily")

LOOKBACK = 10   # 回补窗口：向前看 N 个交易日补失败


def _targets(task: str, end_date: str) -> list[str]:
    dates = calendar.get_trade_dates(
        calendar.get_trade_dates("20050101", end_date)[-LOOKBACK], end_date)
    return synclog.pending_dates(task, dates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标交易日 YYYYMMDD，默认今天/最近交易日")
    args = ap.parse_args()

    today = args.date or datetime.now().strftime("%Y%m%d")
    trade_days = calendar.get_trade_dates("20050101", today)
    if not trade_days:
        log.error("交易日历为空，请先运行 backfill --phases p1")
        return
    end_date = today if calendar.is_trade_date(today) else trade_days[-1]
    log.info("日度任务目标日：%s", end_date)

    client = get_client()
    intervals = st_flag.st_intervals(reload=True)

    q_todo = _targets("daily_quote", end_date)
    log.info("行情待处理：%s", q_todo)
    for d in q_todo:
        fact_daily.sync_day(client, d, intervals)

    b_todo = _targets("daily_basic", end_date)
    log.info("估值待处理：%s", b_todo)
    for d in b_todo:
        fact_daily_basic.sync_day(client, d)

    log.info("日度任务完成")


if __name__ == "__main__":
    main()
