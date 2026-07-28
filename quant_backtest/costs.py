"""交易成本模型（A 股实际费率）。

| 项目   | 费率                        | 方向 |
|--------|-----------------------------|------|
| 佣金   | 万 2.5，最低 5 元           | 双边 |
| 印花税 | 千 1；2023-08-28 起 万 5    | 卖出 |
| 过户费 | 万 0.1                      | 双边 |
| 滑点   | 万 5（可配）                | 双边 |

`CostModel(zero=True)` 一键零成本，用于输出零成本对照。
"""
from __future__ import annotations

from dataclasses import dataclass

STAMP_CHANGE_DATE = "20230828"      # 印花税由千1 下调至万5


@dataclass
class CostModel:
    commission_rate: float = 2.5e-4
    commission_min: float = 5.0
    stamp_before: float = 1.0e-3
    stamp_after: float = 5.0e-4
    transfer_rate: float = 1.0e-5
    slippage_rate: float = 5.0e-4
    zero: bool = False

    def _commission(self, amount: float) -> float:
        return max(amount * self.commission_rate, self.commission_min) if amount > 0 else 0.0

    def stamp_rate(self, date: str) -> float:
        return self.stamp_after if str(date) >= STAMP_CHANGE_DATE else self.stamp_before

    def buy_cost(self, amount: float, date: str) -> float:
        if self.zero or amount <= 0:
            return 0.0
        return (self._commission(amount)
                + amount * self.transfer_rate
                + amount * self.slippage_rate)

    def sell_cost(self, amount: float, date: str) -> float:
        if self.zero or amount <= 0:
            return 0.0
        return (self._commission(amount)
                + amount * self.stamp_rate(date)
                + amount * self.transfer_rate
                + amount * self.slippage_rate)
