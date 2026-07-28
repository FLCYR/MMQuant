"""回测主循环：逐日盯市，调仓日的**下一交易日开盘**执行。

与策略解耦：引擎只依赖一个可调用对象 `weights_fn(T) -> Series[ts_code->weight]`，
任何策略都能插入。`weights_fn` 收到的是**信号日 T**，成交发生在 T 的下一交易日，
从结构上杜绝前视。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from quant_backtest.costs import CostModel
from quant_backtest.execution import Trade, liquidate, rebalance
from quant_backtest.market import MarketData
from quant_backtest.portfolio import Portfolio


@dataclass
class BacktestResult:
    nav: pd.Series                      # 日频净值
    cash: pd.Series
    trades: pd.DataFrame
    turnover: pd.Series                 # 每个执行日的单边换手（双边成交额/2 ÷ 调仓前净值）
    exec_weights: dict = field(default_factory=dict)   # 执行日 -> 实际权重

    @property
    def total_cost(self) -> float:
        return float(self.trades["cost"].sum()) if len(self.trades) else 0.0


def run(weights_fn, market: MarketData, rebal_dates: list[str],
        costs: CostModel | None = None, init_cash: float = 1e7,
        strict: bool = True) -> BacktestResult:
    costs = costs or CostModel()
    dates = market.dates
    pos = {d: i for i, d in enumerate(dates)}

    # 信号日 T → 执行日（T 的下一交易日）
    exec_map: dict[str, str] = {}
    for T in rebal_dates:
        i = pos.get(str(T))
        if i is None or i + 1 >= len(dates):
            continue
        exec_map[dates[i + 1]] = str(T)

    pf = Portfolio(init_cash)
    nav_rows, trades_all, turn_rows, exec_w = [], [], [], {}

    for d in dates:
        # 1) 退市/长期无行情 → 按最后可得价强平
        for code in list(pf.units):
            if market.delisted_by(d, code):
                lq = market.last_quote.get(code)
                price = market.adj_close.at[lq, code] if lq else None
                t = liquidate(pf, code, price, d, costs)
                if t:
                    trades_all.append(t)

        # 2) 调仓执行（T+1 开盘）
        if d in exec_map:
            T = exec_map[d]
            w = weights_fn(T)                       # 只允许使用 <=T 的信息
            if w is not None and len(w) > 0:
                nav_before = pf.value(market.open_prices(d))
                ts = rebalance(pf, w, d, market, costs)
                trades_all.extend(ts)
                # 单边换手：买+卖双边成交额取半（≈ ½·Σ|Δw|），与"单边"口径一致
                traded = sum(t.amount for t in ts)
                turn_rows.append((d, traded / 2.0 / nav_before if nav_before > 0 else 0.0))
                exec_w[d] = pf.weights(market.open_prices(d))

        # 3) 逐日盯市（停牌股按最后价）
        marks = market.mark_prices(d)
        nav_rows.append((d, pf.value(marks), pf.cash))
        if strict and pf.cash < -1e-6:
            raise AssertionError(f"{d}: 现金为负 {pf.cash}")

    nav_df = pd.DataFrame(nav_rows, columns=["trade_date", "nav", "cash"]).set_index("trade_date")
    tdf = pd.DataFrame([t.__dict__ for t in trades_all]) if trades_all else \
        pd.DataFrame(columns=["date", "ts_code", "side", "amount", "cost"])
    turn = (pd.DataFrame(turn_rows, columns=["trade_date", "turnover"])
            .set_index("trade_date")["turnover"] if turn_rows else pd.Series(dtype=float))
    return BacktestResult(nav=nav_df["nav"], cash=nav_df["cash"], trades=tdf,
                          turnover=turn, exec_weights=exec_w)
