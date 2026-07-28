"""动量/反转因子（后复权价窗口）。

REV20：过去 20 交易日收益取负——A 股短期反转显著（direction=+1）。
MOM120：过去 120 日收益、跳过最近 20 日（避开短期反转）。
"""
from __future__ import annotations

from quant_factor.factors.base import register, query_at_dates, DQ

_ADJ = f"SELECT ts_code, trade_date, close*adj_factor AS ac FROM {{dq}} WHERE is_suspended=0 AND close IS NOT NULL"


@register("REV20", "reversal", +1)
def rev20(dates):
    inner = f"""
        WITH d AS ({_ADJ.format(dq=DQ())})
        SELECT ts_code, trade_date,
               -(ac / LAG(ac, 20) OVER w - 1) AS value
        FROM d WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
    """
    return query_at_dates(inner, dates)


@register("MOM120", "momentum", +1)
def mom120(dates):
    inner = f"""
        WITH d AS ({_ADJ.format(dq=DQ())})
        SELECT ts_code, trade_date,
               LAG(ac, 20) OVER w / LAG(ac, 140) OVER w - 1 AS value
        FROM d WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date)
    """
    return query_at_dates(inner, dates)
