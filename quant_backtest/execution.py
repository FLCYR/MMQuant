"""调仓撮合：把目标权重变成实际成交，受可交易性与现金约束。

顺序：先卖后买（贴近实盘资金流）。
- 卖不掉（跌停/停牌）→ 保留持仓，权重顺延到下期。
- 买不进（涨停/停牌/无价）→ 不买，资金留作现金。
- 买入总额受现金约束，超出时按比例缩放。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant_backtest.costs import CostModel
from quant_backtest.market import MarketData
from quant_backtest.portfolio import Portfolio

_EPS = 1e-6
MIN_TRADE = 100.0     # 最小交易金额（元）：现实中不会为几十元调仓；亦避免最低佣金吞噬碎股


@dataclass
class Trade:
    date: str
    ts_code: str
    side: str          # BUY / SELL
    amount: float      # 成交金额（不含费用）
    cost: float        # 费用


def _capped_sell_cost(costs: CostModel, amount: float, date: str) -> float:
    """费用不超过成交额：最低佣金遇到碎股时否则会造成负现金。"""
    return min(costs.sell_cost(amount, date), amount)


def rebalance(pf: Portfolio, target_w: pd.Series, date: str,
              market: MarketData, costs: CostModel,
              min_trade: float = MIN_TRADE) -> list[Trade]:
    opens = market.open_prices(date)
    nav = pf.value(opens)
    if nav <= 0:
        return []

    target_val = {c: float(w) * nav for c, w in target_w.items() if float(w) > 0}
    trades: list[Trade] = []

    # ---------------- 卖出 ----------------
    for code in list(pf.units):
        p = opens.get(code)
        if p is None or pd.isna(p) or p <= 0:
            continue                                   # 无价：退市由 engine 处理
        cur = pf.units[code] * float(p)
        tgt = target_val.get(code, 0.0)
        delta = cur - tgt
        if delta <= _EPS:
            continue
        full_exit = tgt <= _EPS
        if not full_exit and delta < min_trade:
            continue                                   # 小额调整不值得交易
        if not market.is_sellable(date, code):
            continue                                   # 跌停/停牌卖不掉 → 继续持有
        amount = delta
        cost = _capped_sell_cost(costs, amount, date)
        pf.add_units(code, -amount / float(p))
        pf.cash += amount - cost
        trades.append(Trade(date, code, "SELL", amount, cost))

    # ---------------- 买入 ----------------
    desired: dict[str, float] = {}
    for code, tgt in target_val.items():
        p = opens.get(code)
        if p is None or pd.isna(p) or p <= 0:
            continue
        if not market.is_buyable(date, code):
            continue                                   # 涨停/停牌买不进
        cur = pf.units.get(code, 0.0) * float(p)
        if tgt - cur >= max(_EPS, min_trade):           # 小额加仓不值得交易
            desired[code] = tgt - cur

    total = sum(desired.values())
    if total > _EPS and pf.cash > _EPS:
        est_rate = costs.buy_cost(total, date) / total if total > 0 else 0.0
        affordable = pf.cash / (1.0 + est_rate)
        scale = min(1.0, affordable / total)
        for code, amt in desired.items():
            a = amt * scale
            if a <= _EPS:
                continue
            cost = costs.buy_cost(a, date)
            if a + cost > pf.cash:                     # 逐笔兜底，杜绝负现金
                a = pf.cash - costs.buy_cost(pf.cash, date)
                if a <= _EPS:
                    continue
                cost = costs.buy_cost(a, date)
            p = float(opens[code])
            pf.add_units(code, a / p)
            pf.cash -= (a + cost)
            trades.append(Trade(date, code, "BUY", a, cost))

    return trades


def liquidate(pf: Portfolio, code: str, price: float, date: str,
              costs: CostModel) -> Trade | None:
    """强制平仓（退市等），按给定价格全部卖出。"""
    u = pf.units.get(code)
    if not u or price is None or pd.isna(price) or price <= 0:
        return None
    amount = u * float(price)
    cost = _capped_sell_cost(costs, amount, date)
    pf.add_units(code, -u)
    pf.cash += amount - cost
    return Trade(date, code, "SELL", amount, cost)
