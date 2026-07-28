"""波动因子：VOL20 = 过去 20 交易日日收益率标准差。低波异象（direction=-1）。"""
from __future__ import annotations

from quant_factor.factors.base import register, query_at_dates, DQ


@register("VOL20", "volatility", -1)
def vol20(dates):
    inner = f"""
        WITH d AS (SELECT ts_code, trade_date, close*adj_factor AS ac
                   FROM {DQ()} WHERE is_suspended=0 AND close IS NOT NULL),
             r AS (SELECT ts_code, trade_date,
                          ac / LAG(ac, 1) OVER w - 1 AS ret
                   FROM d WINDOW w AS (PARTITION BY ts_code ORDER BY trade_date))
        SELECT ts_code, trade_date,
               stddev_samp(ret) OVER (PARTITION BY ts_code ORDER BY trade_date
                                      ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS value
        FROM r
    """
    return query_at_dates(inner, dates)
