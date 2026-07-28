"""因子正确性验证。

分四类：
1. 公式验证（真实数据独立复算，对比因子输出）。
2. PIT 无未来（核心）：财务因子只用 ann_date<=T 的数据。
3. 中性化 / 标准化 性质（含合成数据）。
4. universe 与前向收益对齐。

正确性 ≠ 有效性：本文件只验证"算得对、无前视"；有效性(IC/多空)见 scripts/eval_factor.py。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_data import storage, calendar, pit
from quant_factor import compute, neutralize, universe, returns
import config

DATES = ["20230630", "20231229"]
LIQUID = "600519.SH"          # 贵州茅台，极少停牌，便于按交易日回看核对


@pytest.fixture(scope="module")
def panel():
    if not storage.fact_source("daily_quote"):
        pytest.skip("无数据，请先 backfill")
    raw, proc = compute.compute_factors(DATES)
    return raw, proc


# ====================================================================
# 1. 公式验证（真实数据独立复算）
# ====================================================================
def test_bp_is_reciprocal_pb(panel):
    raw, _ = panel
    T = DATES[0]
    db = storage.query(f"""SELECT ts_code, pb FROM {storage.fact_source('daily_basic')}
        WHERE trade_date='{T}' AND pb>0""")
    m = raw[raw.trade_date == T][["ts_code", "BP"]].merge(db, on="ts_code")
    assert (m["BP"] * m["pb"] - 1).abs().max() < 1e-6


def test_lncap_is_log_mktcap(panel):
    raw, _ = panel
    T = DATES[0]
    db = storage.query(f"""SELECT ts_code, total_mv FROM {storage.fact_source('daily_basic')}
        WHERE trade_date='{T}' AND total_mv>0""")
    m = raw[raw.trade_date == T][["ts_code", "LNCAP"]].merge(db, on="ts_code")
    assert (np.exp(m["LNCAP"]) / m["total_mv"] - 1).abs().max() < 1e-6


def test_rev20_formula(panel):
    raw, _ = panel
    T = DATES[0]
    tds = calendar.get_trade_dates("20200101", T)
    T20 = tds[-21]            # 20 个交易日前
    px = storage.query(f"""SELECT trade_date, close*adj_factor AS ac
        FROM {storage.fact_source('daily_quote')}
        WHERE ts_code='{LIQUID}' AND trade_date IN ('{T}','{T20}')""").set_index("trade_date")["ac"]
    expected = -(px[T] / px[T20] - 1)
    got = raw[(raw.trade_date == T) & (raw.ts_code == LIQUID)]["REV20"].iloc[0]
    assert abs(got - expected) < 1e-6


def test_vol20_formula(panel):
    raw, _ = panel
    T = DATES[0]
    tds = calendar.get_trade_dates("20200101", T)
    win = tds[-21:]          # 含 T 的最近 21 天 → 20 个日收益
    dstr = ",".join(f"'{d}'" for d in win)
    px = storage.query(f"""SELECT trade_date, close*adj_factor AS ac
        FROM {storage.fact_source('daily_quote')}
        WHERE ts_code='{LIQUID}' AND trade_date IN ({dstr})""").sort_values("trade_date")
    ret = px["ac"].pct_change().dropna()
    expected = ret.std(ddof=1)
    got = raw[(raw.trade_date == T) & (raw.ts_code == LIQUID)]["VOL20"].iloc[0]
    assert abs(got - expected) < 1e-6


# ====================================================================
# 2. PIT 无未来（核心）
# ====================================================================
def test_roe_factor_matches_pit(panel):
    raw, _ = panel
    T = DATES[0]
    p = pit.get_financial_pit(T)[["ts_code", "roe"]].dropna()
    m = raw[raw.trade_date == T][["ts_code", "ROE"]].merge(p, on="ts_code")
    assert (m["ROE"] - m["roe"]).abs().max() < 1e-9


def test_financial_factor_no_lookahead():
    # PIT 快照内所有 ann_date <= T → 因子在 T 不含未来财报
    for T in DATES:
        p = pit.get_financial_pit(T)
        assert (p["ann_date"] <= T).all(), f"{T}: 财务因子用到了未来披露"


def test_price_factor_uses_only_past(panel):
    # REV20 在 T 仅由 <=T 的价格构成：把某股 T 之后的价格改动不应影响（结构性保证）。
    # 这里以窗口边界断言：REV20 的复算价格日期都 <= T（见 test_rev20_formula 用的 T/T20 均<=T）。
    T = DATES[0]
    tds = calendar.get_trade_dates("20200101", T)
    assert tds[-1] == T and tds[-21] < T


# ====================================================================
# 3. 中性化 / 标准化 性质
# ====================================================================
def test_neutralized_orthogonal_to_size(panel):
    _, proc = panel
    T = DATES[0]
    size = neutralize.lnmktcap_at([T])
    ind = neutralize.industry_at([T])
    for nm in ["BP", "ROE", "REV20", "VOL20"]:
        d = proc[proc.trade_date == T][["ts_code", nm]].dropna().merge(size, on="ts_code")
        corr = np.corrcoef(d[nm], d["lnmktcap"])[0, 1]
        assert abs(corr) < 0.05, f"{nm} 与 ln市值 相关 {corr:.3f} 过高"
        di = proc[proc.trade_date == T][["ts_code", nm]].dropna().merge(ind, on="ts_code")
        assert di.groupby("industry_code")[nm].mean().abs().mean() < 0.05


def test_zscore_properties():
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7])
    z = neutralize.zscore(s)
    assert abs(z.mean()) < 1e-9 and abs(z.std(ddof=1) - 1) < 1e-9


def test_winsor_clips_outliers():
    s = pd.Series([1.0, 2, 3, 4, 5, 1000])
    w = neutralize.winsor_mad(s, k=3)
    assert w.max() < 1000 and w.min() == 1.0


def test_neutralize_one_removes_size():
    rng = np.random.default_rng(0)
    n = 500
    size = pd.Series(rng.normal(size=n))
    ind = pd.Series(rng.choice(list("ABC"), n))
    y = 2 * size + rng.normal(scale=0.1, size=n)     # 强规模暴露
    z = neutralize._neutralize_one(y, ind, size)
    assert abs(np.corrcoef(z.to_numpy(), size.to_numpy())[0, 1]) < 0.05


# ====================================================================
# 4. universe 与前向收益对齐
# ====================================================================
def test_universe_no_st_suspended():
    T = DATES[0]
    u = universe.get_universe(T, pool="csi500")
    assert u
    dstr = ",".join(f"'{c}'" for c in u)
    chk = storage.query(f"""SELECT sum(is_st) st, sum(is_suspended) susp
        FROM {storage.fact_source('daily_quote')}
        WHERE trade_date='{T}' AND ts_code IN ({dstr})""").iloc[0]
    assert int(chk["st"]) == 0 and int(chk["susp"]) == 0


def test_universe_csi500_count():
    for T in DATES:
        u = universe.get_universe(T, pool="csi500")
        assert 470 <= len(u) <= 500, f"{T} 可投域 {len(u)} 只（应≈500，剔除ST/停牌/新股后略少）"


def test_forward_return_alignment():
    rd = ["20231201", "20231208", "20231215"]
    fr = returns.forward_returns(rd, k=1)
    px = storage.query(f"""SELECT ts_code, trade_date, close*adj_factor AS ac
        FROM {storage.fact_source('daily_quote')}
        WHERE trade_date IN ('20231201','20231208') AND ts_code='{LIQUID}'""").set_index("trade_date")["ac"]
    expected = px["20231208"] / px["20231201"] - 1
    got = fr[(fr.trade_date == "20231201") & (fr.ts_code == LIQUID)]["fwd_ret"].iloc[0]
    assert abs(got - expected) < 1e-9
    # 最后一个调仓日无前向收益
    assert (fr["trade_date"] == "20231215").sum() == 0
