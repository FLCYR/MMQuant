"""规模因子：LNCAP = ln(总市值)。历史上 A 股偏小盘（direction=-1）。"""
from __future__ import annotations

from quant_factor.factors.base import register, query_at_dates, DB


@register("LNCAP", "size", -1)
def lncap(dates):
    return query_at_dates(f"SELECT ts_code, trade_date, ln(total_mv) AS value FROM {DB()} WHERE total_mv>0", dates)
