"""回测引擎可靠性测试：用注入的合成行情做确定性断言。

这是回测可信度的基石——涨跌停/停牌/退市/成本/无前视等分支都在这里被锁死。
每个用例的正确答案都可解析验证，不依赖真实数据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_backtest import engine
from quant_backtest.costs import STAMP_CHANGE_DATE, CostModel
from quant_backtest.market import MarketData
from quant_backtest.portfolio import Portfolio

ZERO = CostModel(zero=True)
CASH0 = 1_000_000.0


def make_market(dates, codes, opens, closes, buyable=True, sellable=True) -> MarketData:
    idx, cols = pd.Index(dates), pd.Index(codes)
    mk = lambda v: (pd.DataFrame(v, index=idx, columns=cols) if not np.isscalar(v)
                    else pd.DataFrame(v, index=idx, columns=cols))
    return MarketData(adj_open=mk(opens), adj_close=mk(closes),
                      buyable=mk(buyable).astype(bool), sellable=mk(sellable).astype(bool))


def const_weights(w: dict):
    return lambda T: pd.Series(w, dtype=float)


# ====================================================================
# 1. 解析可验证：买入持有，零成本 → NAV 精确等于价格比
# ====================================================================
def test_buy_and_hold_matches_price_ratio():
    dates = ["20200102", "20200103", "20200106"]
    px = [[10.0], [11.0], [12.0]]
    mkt = make_market(dates, ["A"], px, px)
    res = engine.run(const_weights({"A": 1.0}), mkt, ["20200102"], ZERO, CASH0)
    # d2 开盘 11 全额买入 → d2 收盘 NAV 不变；d3 收盘 12 → NAV = CASH0*12/11
    assert res.nav["20200103"] == pytest.approx(CASH0, rel=1e-12)
    assert res.nav["20200106"] == pytest.approx(CASH0 * 12 / 11, rel=1e-12)


# ====================================================================
# 2. 现金守恒 / 不出现负现金
# ====================================================================
def test_two_stock_nav_is_analytic():
    """零成本、单次调仓：NAV 可解析求出，逐日核对。"""
    dates = ["20200102", "20200103", "20200106"]
    px = [[10.0, 20.0], [10.0, 20.0], [12.0, 19.0]]      # d2 开盘建仓价 10 / 20
    mkt = make_market(dates, ["A", "B"], px, px)
    res = engine.run(const_weights({"A": 0.5, "B": 0.5}), mkt, ["20200102"], ZERO, CASH0)
    uA, uB = CASH0 * 0.5 / 10.0, CASH0 * 0.5 / 20.0      # 50000 / 25000 份额
    assert res.nav["20200103"] == pytest.approx(CASH0, rel=1e-12)
    assert res.nav["20200106"] == pytest.approx(uA * 12.0 + uB * 19.0, rel=1e-12)
    assert res.cash["20200106"] == pytest.approx(0.0, abs=1e-6)   # 满仓，无残留现金


def test_no_negative_cash_with_real_costs():
    dates = ["20200102", "20200103", "20200106", "20200107"]
    px = [[10.0, 20.0], [11.0, 19.0], [12.0, 21.0], [12.5, 20.0]]
    mkt = make_market(dates, ["A", "B"], px, px)
    res = engine.run(const_weights({"A": 0.5, "B": 0.5}), mkt,
                     ["20200102", "20200106"], CostModel(), CASH0)
    assert (res.cash >= -1e-6).all()
    assert res.total_cost > 0


# ====================================================================
# 3. 无前视：改变执行日之后的价格，不影响当期成交与净值
# ====================================================================
def test_no_lookahead():
    dates = ["20200102", "20200103", "20200106"]
    base = [[10.0], [11.0], [12.0]]
    alt = [[10.0], [11.0], [99.0]]           # 只改 d3
    r1 = engine.run(const_weights({"A": 1.0}), make_market(dates, ["A"], base, base),
                    ["20200102"], ZERO, CASH0)
    r2 = engine.run(const_weights({"A": 1.0}), make_market(dates, ["A"], alt, alt),
                    ["20200102"], ZERO, CASH0)
    assert r1.nav["20200103"] == pytest.approx(r2.nav["20200103"])
    assert len(r1.trades) == len(r2.trades)


def test_weights_fn_receives_signal_date_not_exec_date():
    dates = ["20200102", "20200103"]
    px = [[10.0], [10.0]]
    seen = []

    def wf(T):
        seen.append(T)
        return pd.Series({"A": 1.0})

    engine.run(wf, make_market(dates, ["A"], px, px), ["20200102"], ZERO, CASH0)
    assert seen == ["20200102"]               # 信号日，而非执行日 20200103


# ====================================================================
# 4/5/6. 涨停买不进 / 跌停卖不掉 / 停牌不可交易
# ====================================================================
def test_limit_up_blocks_buy():
    dates = ["20200102", "20200103"]
    px = [[10.0], [11.0]]
    mkt = make_market(dates, ["A"], px, px, buyable=False)   # 执行日涨停
    res = engine.run(const_weights({"A": 1.0}), mkt, ["20200102"], ZERO, CASH0)
    assert len(res.trades) == 0
    assert res.cash["20200103"] == pytest.approx(CASH0)      # 资金原封不动


def test_limit_down_blocks_sell():
    dates = ["20200102", "20200103", "20200106"]
    px = [[10.0], [10.0], [10.0]]
    sell_ok = [[True], [True], [False]]                      # 第二次调仓日跌停
    mkt = make_market(dates, ["A"], px, px, sellable=sell_ok)
    # 先买满，再要求清仓
    w = {"20200102": pd.Series({"A": 1.0}), "20200103": pd.Series(dtype=float)}
    res = engine.run(lambda T: w[T], mkt, ["20200102", "20200103"], ZERO, CASH0)
    sells = res.trades[res.trades.side == "SELL"] if len(res.trades) else res.trades
    assert len(sells) == 0                                   # 卖不掉
    assert res.nav["20200106"] == pytest.approx(CASH0)       # 仍满仓持有


def test_suspended_no_trade():
    dates = ["20200102", "20200103"]
    px = [[10.0], [10.0]]
    mkt = make_market(dates, ["A"], px, px, buyable=False, sellable=False)
    res = engine.run(const_weights({"A": 1.0}), mkt, ["20200102"], ZERO, CASH0)
    assert len(res.trades) == 0 and res.cash["20200103"] == pytest.approx(CASH0)


# ====================================================================
# 7. 成本核算（含印花税 2023-08-28 切换）
# ====================================================================
def test_cost_reduces_nav_exactly():
    dates = ["20200102", "20200103"]
    px = [[10.0], [10.0]]                     # 执行日开盘=收盘，无价格变动
    costs = CostModel(commission_rate=1e-3, commission_min=0.0, stamp_before=0.0,
                      stamp_after=0.0, transfer_rate=0.0, slippage_rate=0.0)
    res = engine.run(const_weights({"A": 1.0}), make_market(dates, ["A"], px, px),
                     ["20200102"], costs, CASH0)
    # 价格未变，净值下降恰为费用
    assert res.nav["20200103"] == pytest.approx(CASH0 - res.total_cost, rel=1e-12)


def test_stamp_duty_switch():
    c = CostModel(commission_rate=0.0, commission_min=0.0, transfer_rate=0.0, slippage_rate=0.0)
    before = c.sell_cost(1_000_000, "20230825")
    after = c.sell_cost(1_000_000, STAMP_CHANGE_DATE)
    assert before == pytest.approx(1_000_000 * 1e-3)
    assert after == pytest.approx(1_000_000 * 5e-4)
    assert c.buy_cost(1_000_000, "20230825") == 0.0        # 印花税仅卖出方向


# ====================================================================
# 8. 退市强平
# ====================================================================
def test_delisted_position_is_liquidated():
    dates = ["20200102", "20200103", "20200106", "20200107"]
    opens = [[10.0], [10.0], [np.nan], [np.nan]]
    closes = [[10.0], [10.0], [np.nan], [np.nan]]          # d3 起无行情 → 退市
    mkt = make_market(dates, ["A"], opens, closes)
    res = engine.run(const_weights({"A": 1.0}), mkt, ["20200102"], ZERO, CASH0)
    # 退市后应已平仓：净值全部为现金，且不再变动
    assert res.nav["20200107"] == pytest.approx(res.cash["20200107"])
    assert res.cash["20200107"] == pytest.approx(CASH0, rel=1e-9)


# ====================================================================
# 9. Portfolio 账本基本性质
# ====================================================================
def test_portfolio_value_and_weights():
    pf = Portfolio(1000.0)
    pf.add_units("A", 10.0)
    prices = pd.Series({"A": 50.0})
    assert pf.value(prices) == pytest.approx(1500.0)
    assert pf.weights(prices)["A"] == pytest.approx(500.0 / 1500.0)
    pf.add_units("A", -10.0)
    assert "A" not in pf.units


def test_equal_weight_csi500_tracks_index():
    """真实数据端到端校验：等权持有中证500 全部成分，日收益应与指数高度相关。"""
    from quant_data import storage
    from quant_backtest import metrics
    from quant_factor import universe
    from quant_factor.rebalance import get_rebalance_dates

    if not storage.fact_source("daily_quote"):
        pytest.skip("无行情数据")
    dates = get_rebalance_dates("20230101", "20231231", "M")
    uf = universe.universe_frame(dates, pool="csi500")
    if uf.empty:
        pytest.skip("无成分数据")
    by_date = {str(T): list(g["ts_code"]) for T, g in uf.groupby("trade_date")}
    mkt = MarketData.from_storage(dates[0], "20231231", sorted(uf["ts_code"].unique()))

    def wf(T):
        c = by_date.get(str(T), [])
        return pd.Series(1.0 / len(c), index=c, dtype=float) if c else pd.Series(dtype=float)

    res = engine.run(wf, mkt, dates, ZERO, 1e8)
    bench = metrics.benchmark_nav(list(res.nav.index))
    if bench.empty:
        pytest.skip("无基准数据")
    r1, r2 = res.nav.pct_change().dropna(), bench.pct_change().dropna()
    idx = r1.index.intersection(r2.index)
    corr = float(r1.loc[idx].corr(r2.loc[idx]))
    assert corr > 0.95, f"等权成分组合与中证500 日收益相关性仅 {corr:.3f}"


def test_partial_rebalance_respects_cash():
    """目标权重之和为 1 时，扣除费用后不应出现负现金。"""
    dates = ["20200102", "20200103"]
    px = [[10.0, 20.0, 30.0], [10.0, 20.0, 30.0]]
    mkt = make_market(dates, ["A", "B", "C"], px, px)
    res = engine.run(const_weights({"A": 0.4, "B": 0.4, "C": 0.2}), mkt,
                     ["20200102"], CostModel(), CASH0)
    assert (res.cash >= -1e-6).all()
    assert res.nav["20200103"] == pytest.approx(CASH0 - res.total_cost, rel=1e-9)
