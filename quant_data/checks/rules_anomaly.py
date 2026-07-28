"""C6xx 异常检测（统计层，跑批后离线执行）。

C601 复权探针是复权错误最有效的探针：复权价（close*adj_factor）在**相邻交易日**
之间日收益率绝对值 > 25% 即高度可疑。用交易日历的 pretrade_date 限定相邻，排除
复牌后的合理大跳；北交所（.BJ，30% 涨跌幅）单独排除。
"""
from __future__ import annotations

import pandas as pd

from quant_data import storage
from quant_data.checks.base import CheckResult


def c601_adj_return_probe(threshold: float = 0.25,
                          list_buffer_days: int = 20) -> tuple[pd.DataFrame, CheckResult]:
    """复权探针。排除项：北交所（30% 涨跌幅）、上市 list_buffer_days 天内
    （新股前 5 个交易日无涨跌停，会有合法巨震；真实复权错误发生在除权日，
    与上市无关，不会被此过滤掩盖）。"""
    src = storage.fact_source("daily_quote")
    if not src:
        return pd.DataFrame(), CheckResult("C601", "ERROR", True, 0, "无数据",
                                           "fact_daily_quote", "ALL")
    calp = str(storage.dim_path("trade_calendar")).replace("\\", "/")
    sbp = str(storage.dim_path("stock_basic")).replace("\\", "/")
    sql = f"""
    WITH q AS (
        SELECT ts_code, CAST(trade_date AS VARCHAR) AS trade_date,
               close * adj_factor AS adj_close
        FROM {src}
        WHERE close IS NOT NULL AND is_suspended = 0 AND adj_factor > 0
    ),
    r AS (
        SELECT ts_code, trade_date, adj_close,
               LAG(adj_close)  OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev,
               LAG(trade_date) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_date
        FROM q
    ),
    cal AS (
        SELECT CAST(cal_date AS VARCHAR) AS cal_date,
               CAST(pretrade_date AS VARCHAR) AS pretrade_date
        FROM read_parquet('{calp}') WHERE exchange = 'SSE'
    ),
    basic AS (
        SELECT ts_code, CAST(list_date AS VARCHAR) AS list_date
        FROM read_parquet('{sbp}')
    )
    SELECT r.ts_code, r.trade_date, r.prev_date,
           r.adj_close, r.prev, r.adj_close / r.prev - 1 AS ret
    FROM r JOIN cal ON r.trade_date = cal.cal_date
           LEFT JOIN basic ON r.ts_code = basic.ts_code
    WHERE r.prev IS NOT NULL
      AND r.prev_date = cal.pretrade_date
      AND r.ts_code NOT LIKE '%.BJ'
      AND (basic.list_date IS NULL OR
           datediff('day', strptime(basic.list_date, '%Y%m%d'),
                    strptime(r.trade_date, '%Y%m%d')) > {list_buffer_days})
      AND abs(r.adj_close / r.prev - 1) > {threshold}
    ORDER BY abs(r.adj_close / r.prev - 1) DESC
    """
    sus = storage.query(sql)
    res = CheckResult("C601", "ERROR", sus.empty, len(sus),
                      str(sus.head().to_dict("records"))[:1000],
                      "fact_daily_quote", "ALL")
    return sus, res
