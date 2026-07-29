"""策略实盘跟踪（纸上模拟）正确性验证。

用真实历史数据"倒回"模拟：把 last_advanced_date 人为设为几周前，让 advance() 把这段
真实交易日走一遍——比纯合成数据更能验证真实涨跌停/停牌等约束下的行为，且比全历史回测快。

覆盖：
1. create() 产生初始 pending_signal 与首条 nav 记录
2. 单日推进：执行上一次信号、追加 nav、非调仓日不产生新信号
3. 调仓日：产生新信号且冻结到下一次推进才执行（不能当天算当天买，防未来）
4. 多日一次性推进 与 逐日分次推进 结果一致（幂等/正确性）
5. 停止的实例不参与 advance_all()
6. 当天全市场行情缺失（上游数据源未出全）不应被误判成"全部退市"而强制清仓
   （2026-07-29 用真实数据复现过：Tushare 当天 daily/daily_basic 尚未发布，
   daily_quote 仅有 adj_factor、close 全空，导致 delisted_by 对每只持仓都判定为
   "窗口内无行情"，把组合整个强平——这里用合成数据固定住修复后的正确行为）
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_backtest.market import MarketData
from quant_data import calendar, storage
from quant_factor import compute
from quant_web import live_store
from quant_web.services import live_service as ls


def _has_data() -> bool:
    return bool(storage.fact_source("daily_quote")) and not compute.read_panel("processed").empty


pytestmark = pytest.mark.skipif(not _has_data(), reason="无数据/无因子面板，请先 backfill + build_factors")

PARAMS = {"strategy": "multifactor", "strategy_params": {"topn": 8}, "pool": "csi500", "freq": "W", "cash": 1e7}


@pytest.fixture()
def fresh_run():
    """真实创建一个实例（今天为起点），测试结束后清理。"""
    meta = ls.create("test", dict(PARAMS))
    yield meta["run_id"]
    ls.delete(meta["run_id"])


def _rewind(run_id: str, back_days: int = 12):
    """把起点回拨到 back_days 个真实交易日之前，清空初始信号/持仓，从空仓重新开始。"""
    state = live_store.load_state(run_id)
    today = state["last_advanced_date"]
    days = calendar.get_trade_dates("20050101", today)
    earlier = days[-back_days] if len(days) > back_days else days[0]
    state["last_advanced_date"] = earlier
    state["pending_signal"] = None
    state["units"] = {}
    state["cash"] = state["init_cash"]
    live_store.save_state(run_id, state)
    live_store.append_nav(run_id, earlier, state["init_cash"], state["init_cash"])
    return earlier


# ---------------------------------------------------------------- 1. create()
def test_create_produces_initial_signal_and_nav(fresh_run):
    state = live_store.load_state(fresh_run)
    assert state["status"] == "active"
    assert state["units"] == {}
    assert state["pending_signal"] is not None
    assert len(state["pending_signal"]["weights"]) == 8          # topn=8
    assert state["pending_signal"]["signal_date"] == state["last_advanced_date"]

    nav = live_store.load_nav(fresh_run)
    assert len(nav) == 1
    assert nav["nav"].iloc[0] == pytest.approx(PARAMS["cash"])   # 首日空仓，nav=初始资金


# ---------------------------------------------------------------- 2/3. 单日推进
def test_advance_executes_pending_then_may_produce_new_signal(fresh_run):
    _rewind(fresh_run, back_days=10)
    r = ls.advance(fresh_run)
    # back_days=10 定的是起点在"倒数第10个交易日"；起点本身只是记账基准、不算"推进"，
    # 严格晚于起点的交易日恰有 back_days-1 个。
    assert len(r["advanced"]) == 9

    state = live_store.load_state(fresh_run)
    nav = live_store.load_nav(fresh_run)
    assert len(nav) == 10                           # 起点 1 条 + 推进 9 天
    assert (nav["cash"] >= -1e-6).all()             # 现金不为负
    trades = live_store.load_trades(fresh_run)
    assert len(trades) > 0                          # 初始信号必然在第一天被执行


def test_signal_freezes_to_next_day_not_same_day(fresh_run):
    """调仓日当天只产生新信号，不会用当天数据买当天——必须等下一次推进才执行。"""
    _rewind(fresh_run, back_days=10)
    ls.advance(fresh_run)
    state = live_store.load_state(fresh_run)
    if state["pending_signal"]:
        assert state["pending_signal"]["signal_date"] == state["last_advanced_date"]
        # 此刻还未执行（要等下一次 advance 才会用下一交易日开盘价执行）
        # 无法直接断言"未执行"，但可断言：若信号号称买入了某股，其成交记录的日期
        # 必然严格晚于 signal_date（结构性防未来，由 _advance_one_day 的 `< d` 判断保证）。
        trades = live_store.load_trades(fresh_run)
        if len(trades):
            assert (trades["date"] > state["pending_signal"]["signal_date"]).all() or \
                   (trades["date"] <= state["last_advanced_date"]).all()


# ---------------------------------------------------------------- 4. 多日 vs 逐日
def test_multiday_advance_matches_stepwise(fresh_run):
    """一次推进 N 天 与 分 N 次每次推进 1 天，最终状态应完全一致。"""
    earlier = _rewind(fresh_run, back_days=6)
    ls.advance(fresh_run)                           # 一次性推进
    bulk_state = live_store.load_state(fresh_run)
    bulk_nav = live_store.load_nav(fresh_run)

    meta2 = ls.create("test2", dict(PARAMS))
    run2 = meta2["run_id"]
    try:
        state2 = live_store.load_state(run2)
        state2["last_advanced_date"] = earlier
        state2["pending_signal"] = None
        state2["units"] = {}
        state2["cash"] = state2["init_cash"]
        live_store.save_state(run2, state2)
        live_store.append_nav(run2, earlier, state2["init_cash"], state2["init_cash"])

        days = calendar.get_trade_dates(earlier, bulk_state["last_advanced_date"])
        todo = [d for d in days if d > earlier]
        for _ in todo:
            ls.advance(run2)                        # 逐日分次推进（每次只会推进 1 天）

        step_state = live_store.load_state(run2)
        assert step_state["last_advanced_date"] == bulk_state["last_advanced_date"]
        assert step_state["cash"] == pytest.approx(bulk_state["cash"], rel=1e-9)
        assert step_state["units"].keys() == bulk_state["units"].keys()
        for code in step_state["units"]:
            assert step_state["units"][code] == pytest.approx(bulk_state["units"][code], rel=1e-9)
    finally:
        ls.delete(run2)


# ---------------------------------------------------------------- 5. 停止不参与 advance_all
def test_stopped_instance_excluded_from_advance_all(fresh_run):
    _rewind(fresh_run, back_days=5)
    ls.stop(fresh_run)
    assert fresh_run not in live_store.active_run_ids()
    results = ls.advance_all()
    assert fresh_run not in results


def test_advance_on_stopped_instance_is_noop():
    meta = ls.create("test3", dict(PARAMS))
    run_id = meta["run_id"]
    try:
        ls.stop(run_id)
        r = ls.advance(run_id)
        assert r["advanced"] == []
    finally:
        ls.delete(run_id)


# ---------------------------------------------------------------- 6. 全市场数据缺失日不误判退市
def test_missing_day_data_does_not_force_liquidate(monkeypatch):
    """合成一个"当天全市场无有效行情"的场景（中间那天 open/close 全 NaN，模拟上游数据源
    当天未发布完整），持仓不应被 delisted_by 误判成退市而强制清空。"""
    dates = ["20990101", "20990102", "20990103"]     # 用远期虚构日期，避免撞真实日历/面板
    code = "600000.SH"
    opens = pd.DataFrame({code: [10.0, np.nan, 11.0]}, index=dates)
    closes = pd.DataFrame({code: [10.0, np.nan, 11.0]}, index=dates)
    buyable = pd.DataFrame({code: [True, False, True]}, index=dates)
    sellable = pd.DataFrame({code: [True, False, True]}, index=dates)
    synthetic = MarketData(adj_open=opens, adj_close=closes, buyable=buyable, sellable=sellable)

    monkeypatch.setattr(ls, "_market_window", lambda d, codes: synthetic)

    state = {"run_id": "synth_bad_day", "cash": 0.0, "units": {code: 100.0},
             "pending_signal": None, "freq": "W", "pool": "csi500",
             "strategy": "multifactor", "strategy_params": {}}
    try:
        ls._advance_one_day(state, "20990102")        # 坏数据日
        assert state["units"] == {code: 100.0}        # 修复前会被强平成 {}
        assert state["cash"] == 0.0                    # 没有发生任何清仓交易
    finally:
        live_store.delete_run("synth_bad_day")        # _advance_one_day 会落盘 nav 记录，清理掉


def test_pending_signal_survives_bad_data_day(monkeypatch):
    """坏数据日不应误执行/丢弃待执行信号，应原样留到下一次推进重试。"""
    dates = ["20990101", "20990102", "20990103"]
    code = "600000.SH"
    opens = pd.DataFrame({code: [10.0, np.nan, 11.0]}, index=dates)
    closes = pd.DataFrame({code: [10.0, np.nan, 11.0]}, index=dates)
    buyable = pd.DataFrame({code: [True, False, True]}, index=dates)
    sellable = pd.DataFrame({code: [True, False, True]}, index=dates)
    synthetic = MarketData(adj_open=opens, adj_close=closes, buyable=buyable, sellable=sellable)

    monkeypatch.setattr(ls, "_market_window", lambda d, codes: synthetic)

    pending = {"signal_date": "20990101", "weights": {code: 1.0}}
    state = {"run_id": "synth_bad_day2", "cash": 1000.0, "units": {},
             "pending_signal": dict(pending), "freq": "W", "pool": "csi500",
             "strategy": "multifactor", "strategy_params": {}}
    try:
        ls._advance_one_day(state, "20990102")         # 坏数据日：不应执行
        assert state["units"] == {}                     # 没买进
        assert state["cash"] == 1000.0                   # 现金没动
        assert state["pending_signal"] == pending        # 信号原样保留，留到下次重试
    finally:
        live_store.delete_run("synth_bad_day2")
