"""流动性因子。

TURN20：过去 20 交易日平均换手率（高换手→低预期收益，direction=-1）。
ILLIQ ：Amihud 非流动性 = mean(|日收益|/成交额, 20日)（低流动性溢价，direction=+1）。
"""
from __future__ import annotations

from quant_factor.factors.base import register, query_at_dates, DQ, DB


@register("TURN20", "liquidity", -1)
def turn20(dates):
    inner = f"""
        WITH t AS (SELECT ts_code, trade_date, turnover_rate
                   FROM {DB()} WHERE turnover_rate IS NOT NULL)
        SELECT ts_code, trade_date,
               avg(turnover_rate) OVER (PARTITION BY ts_code ORDER BY trade_date
                                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS value
        FROM t
    """
    return query_at_dates(inner, dates)


@register("ILLIQ", "liquidity", +1)
def illiq(dates):
    inner = f"""
        WITH d AS (SELECT ts_code, trade_date, close*adj_factor AS ac, amount
                   FROM {DQ()} WHERE is_suspended=0 AND close IS NOT NULL AND amount>0),
             r AS (SELECT ts_code, trade_date,
                          abs(ac / LAG(ac,1) OVER w - 1) / amount AS il
                   FROM d WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date))
        SELECT ts_code, trade_date,
               avg(il) OVER (PARTITION BY ts_code ORDER BY trade_date
                             ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) * 1e6 AS value
        FROM r
    """
    return query_at_dates(inner, dates)
