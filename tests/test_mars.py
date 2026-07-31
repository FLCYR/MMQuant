"""MARS 策略正确性测试：用合成数据锁死信号识别、出场规则与"买入持有不空转"。

不依赖真实数据——每个用例的正确答案都可手工推导。核心逻辑（_scan_one/_plan_exit/
mars_driver）都能脱离 storage 直接喂合成 DataFrame/MarketData 验证。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_backtest.costs import CostModel
from quant_strategy.strategies import mars
from tests.test_backtest import make_market

ZERO = CostModel(zero=True)


def _seq_dates(n: int) -> list[str]:
    return list(pd.bdate_range("2024-01-01", periods=n).strftime("%Y%m%d"))


def _make_bars(dates, *, close, high, low, openp, pre_close, vol, limit_up, susp):
    """构造单只股票的合成日线（adj_factor=1，即不复权，便于手工验证）。"""
    return pd.DataFrame({
        "ts_code": "A.SH", "trade_date": dates,
        "open": openp, "high": high, "low": low, "close": close,
        "pre_close": pre_close, "vol": vol, "adj_factor": 1.0,
        "limit_up": limit_up, "is_suspended": susp,
    })


def _base_params(dates):
    return {"max_positions": 10, "surge_threshold": 600, "pullback_shrink": 0.8,
            "trail_stop": 0.05, "_start": dates[0], "_end": dates[-1]}


# ====================================================================
# 1. 信号识别：突破涨停 + 大盘放量 + 回踩缩量 → T+3 开盘建仓
# ====================================================================
def test_scan_hits_breakout_signal():
    n = 68
    dates = _seq_dates(n)
    T = 60                                    # 涨停基准日
    close = [9.0] * n; high = [9.2] * n; low = [8.8] * n; openp = [9.0] * n
    pre_close = [9.0] * n; vol = [500.0] * n; limit_up = [999.0] * n; susp = [0] * n
    # T 日封涨停、创 60 日新高（突破板）
    pre_close[T] = 10.0; limit_up[T] = 11.0; close[T] = 11.0; high[T] = 11.0
    openp[T] = 10.2; low[T] = 10.1; vol[T] = 1000.0
    p_mid = (10.0 + 11.0) / 2                  # = 10.5
    # T+1, T+2 回踩不破中位 + 缩量
    for k in (T + 1, T + 2):
        close[k] = 10.8; high[k] = 11.0; low[k] = 10.6; openp[k] = 10.7
        pre_close[k] = 11.0; vol[k] = 700.0    # < 0.8×1000
    # 建仓后：涨一波再跌破中位 → 触发硬止损
    close[T + 3] = 11.0; close[T + 4] = 11.5; close[T + 5] = 10.4   # 10.4 < 10.5 破中位
    for k in range(T + 3, n):
        low[k] = close[k] - 0.2; high[k] = close[k] + 0.2; openp[k] = close[k]
        pre_close[k] = close[k - 1]; vol[k] = 600.0

    bars = _make_bars(dates, close=close, high=high, low=low, openp=openp,
                      pre_close=pre_close, vol=vol, limit_up=limit_up, susp=susp)
    pos = {d: i for i, d in enumerate(dates)}
    surge_ok = {dates[T]}                       # 仅 T 日大盘放量达标
    sigs = mars._scan_one(bars, surge_ok, dates, pos, _base_params(dates))

    assert len(sigs) == 1
    assert sigs[0].entry_date == dates[T + 3]   # T+2 确认 → T+3 开盘建仓
    assert sigs[0].exit_date == dates[T + 6]    # T+5 收盘破中位 → 次日清仓
    _ = p_mid


def test_scan_rejects_without_market_surge():
    """条件A（大盘放量）不满足 → 即使个股形态完美也不产生信号。"""
    n = 68
    dates = _seq_dates(n)
    T = 60
    close = [9.0] * n; high = [9.2] * n; low = [8.8] * n; openp = [9.0] * n
    pre_close = [9.0] * n; vol = [500.0] * n; limit_up = [999.0] * n; susp = [0] * n
    pre_close[T] = 10.0; limit_up[T] = 11.0; close[T] = 11.0; high[T] = 11.0; vol[T] = 1000.0
    for k in (T + 1, T + 2):
        close[k] = 10.8; low[k] = 10.6; high[k] = 11.0; openp[k] = 10.7; vol[k] = 700.0
    bars = _make_bars(dates, close=close, high=high, low=low, openp=openp,
                      pre_close=pre_close, vol=vol, limit_up=limit_up, susp=susp)
    pos = {d: i for i, d in enumerate(dates)}
    sigs = mars._scan_one(bars, set(), dates, pos, _base_params(dates))   # 无放量日
    assert sigs == []


def test_scan_rejects_when_pullback_breaks_midline():
    """条件C：回踩跌破中位线 → 拒绝。"""
    n = 68
    dates = _seq_dates(n)
    T = 60
    close = [9.0] * n; high = [9.2] * n; low = [8.8] * n; openp = [9.0] * n
    pre_close = [9.0] * n; vol = [500.0] * n; limit_up = [999.0] * n; susp = [0] * n
    pre_close[T] = 10.0; limit_up[T] = 11.0; close[T] = 11.0; high[T] = 11.0; vol[T] = 1000.0
    close[T + 1] = 10.8; low[T + 1] = 10.6; high[T + 1] = 11.0; openp[T + 1] = 10.7; vol[T + 1] = 700.0
    close[T + 2] = 10.3; low[T + 2] = 10.2; high[T + 2] = 10.9; openp[T + 2] = 10.7; vol[T + 2] = 700.0  # 破 10.5
    bars = _make_bars(dates, close=close, high=high, low=low, openp=openp,
                      pre_close=pre_close, vol=vol, limit_up=limit_up, susp=susp)
    pos = {d: i for i, d in enumerate(dates)}
    sigs = mars._scan_one(bars, {dates[T]}, dates, pos, _base_params(dates))
    assert sigs == []


def test_scan_rejects_without_volume_shrink():
    """条件D：回踩不缩量（对倒出货嫌疑）→ 拒绝。"""
    n = 68
    dates = _seq_dates(n)
    T = 60
    close = [9.0] * n; high = [9.2] * n; low = [8.8] * n; openp = [9.0] * n
    pre_close = [9.0] * n; vol = [500.0] * n; limit_up = [999.0] * n; susp = [0] * n
    pre_close[T] = 10.0; limit_up[T] = 11.0; close[T] = 11.0; high[T] = 11.0; vol[T] = 1000.0
    for k in (T + 1, T + 2):
        close[k] = 10.8; low[k] = 10.6; high[k] = 11.0; openp[k] = 10.7; vol[k] = 900.0   # ≥0.8×1000
    bars = _make_bars(dates, close=close, high=high, low=low, openp=openp,
                      pre_close=pre_close, vol=vol, limit_up=limit_up, susp=susp)
    pos = {d: i for i, d in enumerate(dates)}
    sigs = mars._scan_one(bars, {dates[T]}, dates, pos, _base_params(dates))
    assert sigs == []


# ====================================================================
# 2. 出场规则：硬止损（破中位） vs 移动止盈（峰值回撤）
# ====================================================================
def test_plan_exit_trailing_stop():
    dates = _seq_dates(10)
    close = [10.5, 12.0, 13.0, 14.0, 13.2, 13.0, 13.0, 13.0, 13.0, 13.0]  # 峰14，13.2<14×0.95=13.3
    g = pd.DataFrame({"ts_code": "A.SH", "trade_date": dates, "close": close})
    pos = {d: i for i, d in enumerate(dates)}
    # t2_idx=0 → 建仓从 idx1 起监控；峰值 14(idx3)，idx4=13.2 ≤ 13.3 触发 → 次日 idx5
    exit_d = mars._plan_exit(g, 0, p_mid=10.5, trail=0.05, all_dates=dates, pos=pos, end_date=dates[-1])
    assert exit_d == dates[5]


def test_plan_exit_none_when_never_triggers():
    dates = _seq_dates(6)
    close = [10.6, 10.7, 10.8, 10.9, 11.0, 11.1]     # 一路走高，不破中位也不回撤5%
    g = pd.DataFrame({"ts_code": "A.SH", "trade_date": dates, "close": close})
    pos = {d: i for i, d in enumerate(dates)}
    exit_d = mars._plan_exit(g, 0, p_mid=10.5, trail=0.05, all_dates=dates, pos=pos, end_date=dates[-1])
    assert exit_d is None


# ====================================================================
# 3. 回测驱动：买入持有、无逐日空转、按计划出场
# ====================================================================
def test_driver_buy_hold_sell_no_churn():
    dates = _seq_dates(6)
    # A 在 d2 开盘建仓，d4 计划清仓；持有期价格漂移，不应产生任何中途调仓
    opens = [[10.0], [10.0], [10.0], [11.0], [12.0], [12.0]]
    closes = [[10.0], [10.0], [10.5], [11.5], [12.0], [12.0]]
    mkt = make_market(dates, ["A"], opens, closes)
    strat = mars.MarsStrategy(max_positions=1)
    strat.add(mars._Signal("A", entry_date=dates[2], exit_date=dates[4]))

    res = mars.mars_driver(strat, mkt, dates, ZERO, 1_000_000.0)
    tr = res.trades
    buys = tr[tr.side == "BUY"]; sells = tr[tr.side == "SELL"]
    assert len(buys) == 1 and buys.iloc[0]["date"] == dates[2]      # d2 开盘建仓
    assert len(sells) == 1 and sells.iloc[0]["date"] == dates[4]    # d4 开盘清仓
    # 关键：d3 持有期价格从 10→11 漂移，但不该有任何成交（无"逐日拉回等权"空转）
    assert (tr["date"] == dates[3]).sum() == 0
    # 清仓后组合回到全现金（零成本）
    assert res.nav[dates[5]] == pytest.approx(res.cash[dates[5]], rel=1e-12)


def test_driver_respects_max_positions():
    """空槽约束：max_positions=1 时，同日两个信号只建其一。"""
    dates = _seq_dates(5)
    opens = [[10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [10.0, 20.0], [10.0, 20.0]]
    mkt = make_market(dates, ["A", "B"], opens, opens)
    strat = mars.MarsStrategy(max_positions=1)
    strat.add(mars._Signal("A", entry_date=dates[2], exit_date=None))
    strat.add(mars._Signal("B", entry_date=dates[2], exit_date=None))
    res = mars.mars_driver(strat, mkt, dates, ZERO, 1_000_000.0)
    held = set(res.trades[res.trades.side == "BUY"]["ts_code"])
    assert len(held) == 1                                           # 只建了一个槽位
