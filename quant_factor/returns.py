"""复权收益与前向收益面板。

复权收益一律用后复权价 adj_close = close*adj_factor（与涨跌停用的原始价分离）。
前向收益：调仓日 T 收盘 → 下一调仓日收盘的收益，用于 IC / 分组 / 多空评估。
（执行层面通常再滞后一日买入，评估口径在 evaluate 中注明。）
"""
from __future__ import annotations

import pandas as pd

from quant_data import storage


def adj_close_at(dates: list[str]) -> pd.DataFrame:
    """指定交易日的后复权价（长表：ts_code, trade_date, adj_close）。仅成交行。"""
    src = storage.fact_source("daily_quote")
    if not src or not dates:
        return pd.DataFrame(columns=["ts_code", "trade_date", "adj_close"])
    dstr = ",".join(f"'{d}'" for d in dates)
    return storage.query(f"""
        SELECT ts_code, trade_date, close*adj_factor AS adj_close
        FROM {src}
        WHERE trade_date IN ({dstr})
          AND is_suspended = 0 AND close IS NOT NULL AND adj_factor > 0
    """)


def forward_returns(rebal_dates: list[str], k: int = 1) -> pd.DataFrame:
    """每调仓日 T 到之后第 k 个调仓日的收益（长表：trade_date=T, ts_code, fwd_ret）。

    k=1 即下一调仓日。末尾 k 个调仓日无前向收益，自动剔除；停牌导致缺价的样本剔除。
    """
    ac = adj_close_at(rebal_dates)
    if ac.empty:
        return pd.DataFrame(columns=["trade_date", "ts_code", "fwd_ret"])
    wide = ac.pivot(index="trade_date", columns="ts_code", values="adj_close").sort_index()
    fwd = wide.shift(-k) / wide - 1.0          # 行 T = T→第 k 个调仓日收益
    out = (fwd.reset_index()
              .melt(id_vars="trade_date", var_name="ts_code", value_name="fwd_ret")
              .dropna(subset=["fwd_ret"]))
    return out.reset_index(drop=True)
