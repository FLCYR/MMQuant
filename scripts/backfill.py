"""全历史回补，编排 P1→P7。幂等可重跑：失败/未处理的日期与股票会被自动续补。

用法示例：
    python scripts/backfill.py                 # 跑全部阶段
    python scripts/backfill.py --phases p1 p2  # 只跑指定阶段
    python scripts/backfill.py --start 20200101 --end 20201231
    python scripts/backfill.py --phases p6 --no-resume   # 财务全量重拉
"""
from __future__ import annotations

import _bootstrap  # noqa: F401  (确保 sys.path / 编码)

import argparse
from datetime import datetime

import config
from quant_data import calendar, synclog, storage
from quant_data.client import get_client
from quant_data.fetchers import (
    dim_calendar, dim_stock_basic, st_flag,
    fact_daily, fact_daily_basic,
    dim_index_member, dim_industry,
    fact_financial, fact_index_daily, dim_risk_free,
)
from quant_data.checks import rules_anomaly
from quant_data.log import get_logger

log = get_logger("backfill")

ALL_PHASES = ["p1", "p2", "p4", "p5", "p6", "p7"]


def run_p1(client):
    log.info("===== P1 交易日历 + 股票基本信息 + 更名记录 =====")
    dim_calendar.sync(client)
    dim_stock_basic.sync(client)
    st_flag.sync(client)


def _pending(task, dates, resume):
    return synclog.pending_dates(task, dates) if resume else dates


def run_p2(client, start, end, resume, validate):
    log.info("===== P2 日线行情 fact_daily_quote =====")
    dates = calendar.get_trade_dates(start, end)
    todo = _pending("daily_quote", dates, resume)
    log.info("待处理 %d / %d 个交易日", len(todo), len(dates))
    fact_daily.sync_dates(todo, client, validate)
    log.info("----- P2 完成后执行 C601 复权探针 -----")
    sus, res = rules_anomaly.c601_adj_return_probe()
    log.info("C601 可疑复权跳变 %d 条（passed=%s）", res.fail_count, res.passed)
    if not sus.empty:
        log.warning("C601 样本：\n%s", sus.head(20).to_string(index=False))


def run_p4(client, start, end, resume, validate):
    log.info("===== P4 每日估值市值 fact_daily_basic =====")
    dates = calendar.get_trade_dates(start, end)
    todo = _pending("daily_basic", dates, resume)
    log.info("待处理 %d / %d 个交易日", len(todo), len(dates))
    fact_daily_basic.sync_dates(todo, client, validate)


def run_p5(client, start, end):
    log.info("===== P5 成分股 + 行业区间表 =====")
    for index_code in config.UNIVERSE_INDICES:
        dim_index_member.sync(client, index_code=index_code, start=start, end=end)
    dim_industry.sync(client)


def run_p6(client, resume):
    log.info("===== P6 财务数据 fact_financial =====")
    sb = storage.read_dim("stock_basic")
    codes = sb["ts_code"].tolist()
    if resume:
        done = synclog.done_dates("financial")
        codes = [c for c in codes if c not in done]
    log.info("待处理 %d 只股票", len(codes))
    fact_financial.sync_stocks(codes, client)


def run_p7(client, start, end):
    log.info("===== P7 指数日线 + 无风险利率 =====")
    fact_index_daily.sync(client, start=start, end=end)
    dim_risk_free.sync(client, start=start, end=end)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", nargs="*", default=ALL_PHASES,
                    choices=ALL_PHASES, help="要执行的阶段")
    ap.add_argument("--start", default=config.START_DATE)
    ap.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--no-resume", dest="resume", action="store_false",
                    help="忽略同步日志，强制全量重跑")
    ap.add_argument("--no-validate", dest="validate", action="store_false")
    args = ap.parse_args()

    client = get_client()
    t0 = datetime.now()
    log.info("回补开始：phases=%s range=%s~%s resume=%s",
             args.phases, args.start, args.end, args.resume)

    if "p1" in args.phases:
        run_p1(client)
    if "p2" in args.phases:
        run_p2(client, args.start, args.end, args.resume, args.validate)
    if "p4" in args.phases:
        run_p4(client, args.start, args.end, args.resume, args.validate)
    if "p5" in args.phases:
        run_p5(client, args.start, args.end)
    if "p6" in args.phases:
        run_p6(client, args.resume)
    if "p7" in args.phases:
        run_p7(client, args.start, args.end)

    log.info("回补结束，用时 %s", datetime.now() - t0)


if __name__ == "__main__":
    main()
