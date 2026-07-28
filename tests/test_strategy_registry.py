"""策略注册表正确性验证。

覆盖：
1. 注册/枚举：multifactor 已注册，param_schema 字段完整。
2. default_params 与 build 的合并语义：显式传入覆盖默认值，未传的用默认值补全。
3. 未知策略 id 报错清晰。
4. build 产出的策略对象满足 Strategy 协议（target_weights 可调用，返回 Series）。
5. combiner='ic' 分支真实跑通（内部会计算 ic_frame）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from quant_data import storage
from quant_factor import compute
from quant_factor.rebalance import get_rebalance_dates
from quant_strategy import strategies  # noqa: F401  触发注册
from quant_strategy.base import REGISTRY, StrategyContext, build, default_params, list_strategies


def _has_data() -> bool:
    return bool(storage.fact_source("daily_quote")) and not compute.read_panel("processed").empty


pytestmark = pytest.mark.skipif(not _has_data(), reason="无数据/无因子面板，请先 backfill + build_factors")


# ---------------------------------------------------------------- 1. 注册与枚举
def test_multifactor_registered():
    assert "multifactor" in REGISTRY
    specs = list_strategies()
    ids = [s["id"] for s in specs]
    assert "multifactor" in ids
    mf = next(s for s in specs if s["id"] == "multifactor")
    keys = {f["key"] for f in mf["param_schema"]}
    assert keys == {"factors", "topn", "combiner", "industry_neutral", "max_weight"}


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        default_params("nonexistent")
    with pytest.raises(ValueError):
        build("nonexistent", {}, None)


# ---------------------------------------------------------------- 2. 参数合并
def test_default_params_roundtrip():
    d = default_params("multifactor")
    assert d["topn"] == 50 and d["combiner"] == "equal" and d["max_weight"] is None
    assert isinstance(d["factors"], list) and len(d["factors"]) > 0


@pytest.fixture(scope="module")
def ctx():
    dates = get_rebalance_dates("20230101", "20230630", "W")
    panel = compute.read_panel("processed")
    return StrategyContext(dates=dates, pool="csi500",
                           get_panel=lambda: panel, progress=lambda msg, pct: None)


def test_build_merges_partial_params(ctx):
    """只传 topn，其余（factors/combiner/...）应回退默认值。"""
    strat = build("multifactor", {"topn": 15}, ctx)
    assert strat.n == 15
    assert strat.names == default_params("multifactor")["factors"]


# ---------------------------------------------------------------- 3/4. Strategy 协议
def test_built_strategy_satisfies_protocol(ctx):
    strat = build("multifactor", {"topn": 10}, ctx)
    assert callable(strat.target_weights)
    w = strat.target_weights(ctx.dates[5])
    assert isinstance(w, pd.Series)
    if len(w):
        assert (w >= 0).all() and w.sum() <= 1.0 + 1e-6


# ---------------------------------------------------------------- 5. ic 合成器分支
def test_build_ic_combiner(ctx):
    strat = build("multifactor", {"topn": 10, "combiner": "ic"}, ctx)
    w = strat.target_weights(ctx.dates[-1])
    assert isinstance(w, pd.Series)


# ---------------------------------------------------------------- 6. 调仓频率与面板不匹配防护
# bug 背景：面板固定按某一频率（如周频）构建；若调仓日改用另一频率（如月频），
# 绝大多数调仓日在面板里找不到数据，策略会静默地几乎不产生任何交易，且不报错。
# compute.check_dates_coverage 在 _build 里兜底：命中率过低时直接拒绝。
def test_build_rejects_freq_mismatched_dates():
    panel = compute.read_panel("processed")
    monthly = get_rebalance_dates("20230101", "20231231", "M")
    bad_ctx = StrategyContext(dates=monthly, pool="csi500",
                              get_panel=lambda: panel, progress=lambda msg, pct: None)
    with pytest.raises(ValueError, match="命中"):
        build("multifactor", {"topn": 10}, bad_ctx)


def test_check_dates_coverage_passes_when_matched():
    panel = compute.read_panel("processed")
    weekly = get_rebalance_dates("20230101", "20230630", "W")
    compute.check_dates_coverage(weekly, panel)   # 不应抛错


def test_check_dates_coverage_raises_when_mostly_missing():
    panel = compute.read_panel("processed")
    fake_dates = ["19700101", "19700102", "19700103"]   # 面板必然不含这些日期
    with pytest.raises(ValueError):
        compute.check_dates_coverage(fake_dates, panel)
