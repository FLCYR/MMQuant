"""财务数据 Point-in-Time 访问。全项目只应通过本函数访问财务数据。

规则（见文档 §4.2）：
1. 一律以 ann_date <= T 过滤（绝不能用 end_date）——报告期末不等于可见日。
2. 每个报告期取 T 时点可见的最新版本（ann_date 最大）。
3. 每只股票取最新一期。
"""
from __future__ import annotations

import pandas as pd

from quant_data import storage


def get_financial_pit(date: str, report_type: str = "1") -> pd.DataFrame:
    """返回 T=date 时点、每只股票可见的最新一期财务数据。"""
    src = storage.fact_source("financial")
    if not src:
        return pd.DataFrame()
    T = str(date)
    sql = f"""
    WITH visible AS (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY ts_code, end_date
                                  ORDER BY ann_date DESC) AS ver_rank
        FROM {src}
        WHERE ann_date <= '{T}' AND report_type = '{report_type}'
    ),
    latest_ver AS (SELECT * FROM visible WHERE ver_rank = 1),
    ranked AS (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY ts_code
                                  ORDER BY end_date DESC) AS period_rank
        FROM latest_ver
    )
    SELECT * EXCLUDE (ver_rank, period_rank)
    FROM ranked WHERE period_rank = 1
    """
    return storage.query(sql)


def get_financial_history_pit(date: str, report_type: str = "1") -> pd.DataFrame:
    """返回 T 时点可见的、每只股票每个报告期的最新版本（不只最新一期）。

    用于计算同比/环比等需要多期序列的因子。
    """
    src = storage.fact_source("financial")
    if not src:
        return pd.DataFrame()
    T = str(date)
    sql = f"""
    WITH visible AS (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY ts_code, end_date
                                  ORDER BY ann_date DESC) AS ver_rank
        FROM {src}
        WHERE ann_date <= '{T}' AND report_type = '{report_type}'
    )
    SELECT * EXCLUDE (ver_rank) FROM visible WHERE ver_rank = 1
    """
    return storage.query(sql)
