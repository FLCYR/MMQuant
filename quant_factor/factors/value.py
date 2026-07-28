"""价值因子：BP / EP / SP（来自 daily_basic 的估值倒数）。"""
from __future__ import annotations

from quant_factor.factors.base import register, query_at_dates, DB


@register("BP", "value", +1)
def bp(dates):
    return query_at_dates(f"SELECT ts_code, trade_date, 1.0/pb AS value FROM {DB()} WHERE pb>0", dates)


@register("EP", "value", +1)
def ep(dates):
    # 1/pe_ttm 关于盈利收益率单调连续（亏损→负、微利→≈0、高盈利→高）
    return query_at_dates(f"SELECT ts_code, trade_date, 1.0/pe_ttm AS value FROM {DB()} WHERE pe_ttm<>0", dates)


@register("SP", "value", +1)
def sp(dates):
    return query_at_dates(f"SELECT ts_code, trade_date, 1.0/ps_ttm AS value FROM {DB()} WHERE ps_ttm>0", dates)
