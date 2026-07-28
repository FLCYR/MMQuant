"""成长因子：净利润同比 / 营收同比（fina_indicator，经 PIT 取数）。"""
from __future__ import annotations

from quant_factor.factors.base import register, financial_factor


@register("NPYoY", "growth", +1)
def npyoy(dates):
    return financial_factor(dates, "netprofit_yoy")


@register("ORYoY", "growth", +1)
def oryoy(dates):
    return financial_factor(dates, "or_yoy")
