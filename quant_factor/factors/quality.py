"""质量因子：ROE / 毛利率（fina_indicator，经 PIT 取数）。"""
from __future__ import annotations

from quant_factor.factors.base import register, financial_factor


@register("ROE", "quality", +1)
def roe(dates):
    return financial_factor(dates, "roe")


@register("GPM", "quality", +1)
def gpm(dates):
    return financial_factor(dates, "grossprofit_margin")
