"""组合账本：份额 + 现金。

持仓以"份额 u"记账，满足 `市值 = u × adj_close`。用后复权价计价意味着分红送转
被复权因子自动吸收（总收益口径），无需单独建模现金分红。
"""
from __future__ import annotations

import pandas as pd


class Portfolio:
    def __init__(self, cash: float):
        self.cash = float(cash)
        self.units: dict[str, float] = {}

    # ------------------------------------------------------------------
    def positions_value(self, prices: pd.Series) -> float:
        tot = 0.0
        for code, u in self.units.items():
            p = prices.get(code)
            if p is not None and pd.notna(p):
                tot += u * float(p)
        return tot

    def value(self, prices: pd.Series) -> float:
        return self.cash + self.positions_value(prices)

    def weights(self, prices: pd.Series) -> pd.Series:
        nav = self.value(prices)
        if nav <= 0:
            return pd.Series(dtype=float)
        d = {c: u * float(prices.get(c, 0.0) or 0.0) / nav for c, u in self.units.items()}
        return pd.Series(d, dtype=float)

    # ------------------------------------------------------------------
    def add_units(self, code: str, delta: float) -> None:
        u = self.units.get(code, 0.0) + delta
        if u <= 1e-12:
            self.units.pop(code, None)
        else:
            self.units[code] = u

    def held(self) -> list[str]:
        return list(self.units)
