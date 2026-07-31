"""数据管道服务：把脚本级运维操作（日度增量 / 回补 / 校验 / 建因子面板）
包装成可从前端触发的异步 job，并回报进度与结果摘要。

只 import 既有采集器 / 校验 / 因子模块，不改动它们。所有操作幂等可重跑
（与 scripts/ 下同名脚本行为一致），失败按 meta_sync_log 自动续补。
"""
from __future__ import annotations

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
from quant_data.checks import base, rules_financial, rules_interval, rules_anomaly
from quant_factor import compute
from quant_web import jobs

ALL_PHASES = ["p1", "p2", "p4", "p5", "p6", "p7"]
PHASE_LABELS = {
    "p1": "交易日历 / 股票基本信息 / 更名",
    "p2": "日线行情（+复权+涨跌停+停牌+ST）",
    "p4": "每日估值市值",
    "p5": "指数成分 / 行业区间",
    "p6": "财务数据（PIT）",
    "p7": "指数日线 / 无风险利率",
}
LOOKBACK = 10          # 日度增量向前回看的交易日窗口（补失败）


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


# ------------------------------------------------------------------ 只读信息
def info() -> dict:
    """控制台所需的静态信息：可选回补阶段 + 数据新鲜度提示。"""
    src = storage.fact_source("daily_quote")
    last_quote = None
    if src:
        r = storage.query(f"SELECT max(trade_date) mx FROM {src}").iloc[0]
        last_quote = str(r["mx"]) if r["mx"] is not None else None
    # 只取面板的日期摘要（DuckDB 只扫 trade_date 一列），不整表读入——面板有数百 MB，
    # 为了数几个调仓日就把它读进内存会直接把小内存机器撑爆（历史 OOM 根因之一）
    stats = compute.panel_date_stats("processed")
    return {
        "phases": [{"id": p, "label": PHASE_LABELS[p]} for p in ALL_PHASES],
        "universe_indices": config.UNIVERSE_INDICES,
        "last_quote_date": last_quote,
        "factor_panel_dates": stats["dates"],
        "factor_panel_span": ([stats["start"], stats["end"]] if stats["dates"] else None),
    }


# ------------------------------------------------------------------ 日度增量
def daily_update(job_id: str, params: dict) -> dict:
    """等价 scripts/run_daily.py：补近 LOOKBACK 个交易日内未成功的行情与估值。"""
    date = (params or {}).get("date") or _today()
    all_days = calendar.get_trade_dates("20050101", date)
    if not all_days:
        raise RuntimeError("交易日历为空，请先回补 P1")
    end_date = date if calendar.is_trade_date(date) else all_days[-1]
    window = calendar.get_trade_dates(all_days[-LOOKBACK], end_date)

    client = get_client()
    intervals = st_flag.st_intervals(reload=True)
    q_todo = synclog.pending_dates("daily_quote", window)
    b_todo = synclog.pending_dates("daily_basic", window)
    total = (len(q_todo) + len(b_todo)) or 1
    done = 0
    for d in q_todo:
        jobs.progress(job_id, f"行情 {d}", 5 + int(90 * done / total))
        fact_daily.sync_day(client, d, intervals)
        done += 1
    for d in b_todo:
        jobs.progress(job_id, f"估值 {d}", 5 + int(90 * done / total))
        fact_daily_basic.sync_day(client, d)
        done += 1
    return {"end_date": end_date, "quote_days": q_todo, "basic_days": b_todo,
            "message": f"行情补 {len(q_todo)} 天、估值补 {len(b_todo)} 天"}


# ------------------------------------------------------------------ 分阶段回补
def backfill(job_id: str, params: dict) -> dict:
    """等价 scripts/backfill.py。params: phases[], start, end, resume。"""
    p = params or {}
    phases = [x for x in (p.get("phases") or ALL_PHASES) if x in ALL_PHASES]
    if not phases:
        raise ValueError("未指定有效阶段")
    start = p.get("start") or config.START_DATE
    end = p.get("end") or _today()
    resume = bool(p.get("resume", True))
    client = get_client()

    n = len(phases)
    out: dict = {}
    for i, ph in enumerate(phases):
        base_pct = 3 + int(94 * i / n)
        jobs.progress(job_id, f"{ph.upper()} {PHASE_LABELS[ph]}", base_pct)
        if ph == "p1":
            dim_calendar.sync(client)
            dim_stock_basic.sync(client)
            st_flag.sync(client)
            out["p1"] = "ok"
        elif ph == "p2":
            dates = calendar.get_trade_dates(start, end)
            todo = synclog.pending_dates("daily_quote", dates) if resume else dates
            fact_daily.sync_dates(todo, client, True)
            out["p2"] = len(todo)
        elif ph == "p4":
            dates = calendar.get_trade_dates(start, end)
            todo = synclog.pending_dates("daily_basic", dates) if resume else dates
            fact_daily_basic.sync_dates(todo, client, True)
            out["p4"] = len(todo)
        elif ph == "p5":
            for code in config.UNIVERSE_INDICES:
                dim_index_member.sync(client, index_code=code, start=start, end=end)
            dim_industry.sync(client)
            out["p5"] = config.UNIVERSE_INDICES
        elif ph == "p6":
            sb = storage.read_dim("stock_basic")
            codes = sb["ts_code"].tolist()
            if resume:
                doneset = synclog.done_dates("financial")
                codes = [c for c in codes if c not in doneset]
            fact_financial.sync_stocks(codes, client)
            out["p6"] = len(codes)
        elif ph == "p7":
            fact_index_daily.sync(client, start=start, end=end)
            dim_risk_free.sync(client, start=start, end=end)
            out["p7"] = "ok"
    return {"phases": out, "range": [start, end], "resume": resume}


# ------------------------------------------------------------------ 离线校验
def _month_end_trade_days() -> list[str]:
    dates = calendar.get_trade_dates(config.START_DATE)
    by_month: dict[str, str] = {}
    for d in dates:
        by_month[d[:6]] = d
    return sorted(by_month.values())


def run_checks(job_id: str, params: dict) -> dict:
    """等价 scripts/run_checks.py：财务 PIT + 区间表 + C601 复权探针。"""
    results: list = []

    jobs.progress(job_id, "财务 PIT 校验（C5xx）", 15)
    fin = storage.read_fact("financial")
    if not fin.empty:
        r = base.run_checks(fin, rules_financial.RULES, "fact_financial", "ALL")
        base.persist(r)
        results += r

    jobs.progress(job_id, "区间表校验（C505/C506/C206）", 45)
    im = storage.read_dim("index_member")
    if not im.empty:
        r1 = rules_interval.c506_valid_range(im)
        r2 = rules_interval.c505_no_overlap(im, ["index_code", "ts_code"])
        r3 = rules_interval.c206_member_count(im, _month_end_trade_days(), 500, config.CSI500)
        for r in (r1, r2, r3):
            r.table_name = "dim_index_member"
        results += [r1, r2, r3]
    ind = storage.read_dim("industry_member")
    if not ind.empty:
        r4 = rules_interval.c506_valid_range(ind)
        r5 = rules_interval.c505_no_overlap(ind, ["ts_code", "src", "level"])
        for r in (r4, r5):
            r.table_name = "dim_industry_member"
        results += [r4, r5]
    if not im.empty or not ind.empty:
        base.persist([r for r in results if r.table_name.startswith("dim_")])

    jobs.progress(job_id, "C601 复权探针", 78)
    sus, r601 = rules_anomaly.c601_adj_return_probe()
    base.persist([r601])
    results.append(r601)

    summary = [{"check_id": r.check_id, "table": r.table_name,
                "passed": bool(r.passed), "fail_count": int(r.fail_count)} for r in results]
    n_fail = sum(1 for r in results if not r.passed)
    return {"results": summary, "n_fail": n_fail,
            "message": f"{len(results)} 项校验，{n_fail} 项未通过"}


# ------------------------------------------------------------------ 重建因子面板
def build_factors(job_id: str, params: dict) -> dict:
    """等价 scripts/build_factors.py：计算并落地 raw/processed 因子面板。"""
    p = params or {}
    start = p.get("start") or config.START_DATE
    end = p.get("end") or None
    freq = p.get("freq") or config.REBAL_FREQ
    jobs.progress(job_id, "计算并落地因子面板（可能数分钟）", 10)
    raw, proc = compute.build(start, end, freq)
    fac_cols = [c for c in raw.columns if c not in ("trade_date", "ts_code")]
    return {
        "raw_shape": list(raw.shape),
        "proc_shape": list(proc.shape),
        "dates": int(raw["trade_date"].nunique()),
        "span": [str(raw["trade_date"].min()), str(raw["trade_date"].max())],
        "coverage": {c: round(float(proc[c].notna().mean()), 4) for c in fac_cols if c in proc.columns},
        "message": f"面板 {raw.shape[0]} 行 × {len(fac_cols)} 因子",
    }
