"""回测服务：组装既有引擎跑回测并落盘；提供派生视图（行业暴露、滚动超额）。

策略可插拔：本模块只认 `strategy` id + `strategy_params` dict，通过
`quant_strategy.base` 注册表构建，不 import 任何具体策略类——新增策略无需改这里。
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

import config
from quant_backtest import engine, metrics
from quant_backtest.costs import CostModel
from quant_backtest.market import MarketData
from quant_data import storage
from quant_factor import compute, neutralize, universe
from quant_factor.factors import REGISTRY
from quant_factor.rebalance import get_rebalance_dates
from quant_strategy import strategies as _strategies  # noqa: F401  触发所有 @register_strategy
from quant_strategy import base as strat_base
from quant_web import jobs, store

DEFAULT_STRATEGY = "multifactor"


def default_params() -> dict:
    return {
        "start": "20160101",
        "end": datetime.now().strftime("%Y%m%d"),
        "freq": config.REBAL_FREQ,
        "pool": config.UNIV_POOL,
        "cash": config.INIT_CASH,
        "strategy": DEFAULT_STRATEGY,
        "strategy_params": strat_base.default_params(DEFAULT_STRATEGY),
    }


def available_factors() -> list[dict]:
    return [{"name": n, "style": f.style, "direction": f.direction} for n, f in REGISTRY.items()]


def available_strategies() -> list[dict]:
    return strat_base.list_strategies()


def available_industries() -> list[dict]:
    """SW 一级行业清单（供前端股票池行业筛选）。见 data_service.list_industries。"""
    from quant_web.services import data_service
    return data_service.list_industries()


# ------------------------------------------------------------------ 运行
def run_and_save(job_id: str, params: dict) -> dict:
    p = {**default_params(), **(params or {})}
    cash = float(p["cash"])
    strategy_id = p.get("strategy") or DEFAULT_STRATEGY
    strategy_params = {**strat_base.default_params(strategy_id), **(p.get("strategy_params") or {})}
    p["strategy"] = strategy_id
    p["strategy_params"] = strategy_params          # 落盘的是合并默认值后的完整参数

    jobs.progress(job_id, "准备调仓日", 5)
    dates = get_rebalance_dates(p["start"], p["end"], p["freq"])
    if len(dates) < 3:
        raise ValueError("调仓日过少，请扩大区间")

    jobs.progress(job_id, "准备行情数据", 30)
    uf = universe.universe_frame(dates, pool=p["pool"])
    codes = sorted(uf["ts_code"].unique())
    mkt = MarketData.from_storage(dates[0], p["end"], codes)

    jobs.progress(job_id, "构建策略", 40)
    _panel_cache: dict[str, pd.DataFrame] = {}

    def get_panel() -> pd.DataFrame:
        if "df" not in _panel_cache:
            _panel_cache["df"] = compute.read_panel("processed")
        return _panel_cache["df"]

    ctx = strat_base.StrategyContext(
        dates=dates, pool=p["pool"], get_panel=get_panel,
        progress=lambda msg, pct: jobs.progress(job_id, msg, pct),
    )
    strat = strat_base.build(strategy_id, strategy_params, ctx)

    jobs.progress(job_id, "回测（含成本）", 55)
    res = engine.run(strat.target_weights, mkt, dates, CostModel(), cash)
    jobs.progress(job_id, "回测（零成本对照）", 75)
    res0 = engine.run(strat.target_weights, mkt, dates, CostModel(zero=True), cash)

    jobs.progress(job_id, "汇总绩效", 88)
    idx = list(res.nav.index)
    bench = metrics.benchmark_nav(idx)
    rf = metrics.load_rf(idx)
    perfs = [
        metrics.summarize(res.nav, "含成本", bench, rf, res.turnover, res.total_cost, cash),
        metrics.summarize(res0.nav, "零成本", bench, rf, res0.turnover, 0.0, cash),
    ]
    if len(bench):
        perfs.append(metrics.summarize(bench * cash, "中证500", None, rf))

    nav_df = pd.DataFrame({"nav": res.nav, "cash": res.cash, "nav_zero": res0.nav})
    if len(bench):
        nav_df["bench"] = (bench * cash).reindex(nav_df.index).ffill()

    rows = []
    for d, w in res.exec_weights.items():
        for code, wt in w.items():
            if wt and wt > 0:
                rows.append((str(d), code, float(wt)))
    positions = pd.DataFrame(rows, columns=["trade_date", "ts_code", "weight"])

    jobs.progress(job_id, "落盘", 95)
    run_id = store.new_run_id(p)
    from quant_web.serialize import dataclass_dict
    meta = store.save_run(run_id, p, nav_df, res.trades, positions, res.turnover,
                          [dataclass_dict(x) for x in perfs])
    return meta


# ------------------------------------------------------------------ 派生视图
def rolling_excess(run_id: str, window: int = 252) -> dict:
    """滚动窗口超额年化：看策略是否衰减（比全样本 IR 更有决策价值）。"""
    nav = store.load_nav(run_id)
    if nav.empty or "bench" not in nav.columns:
        return {"x": [], "y": []}
    r = nav["nav"].pct_change()
    b = nav["bench"].pct_change()
    ex = (r - b).rolling(window).mean() * config.TRADING_DAYS_PER_YEAR
    ex = ex.dropna()
    return {"x": [str(i) for i in ex.index], "y": [float(v) for v in ex.to_numpy()]}


def drawdown(run_id: str, col: str = "nav") -> dict:
    nav = store.load_nav(run_id)
    if nav.empty or col not in nav.columns:
        return {"x": [], "y": []}
    s = nav[col].astype(float)
    dd = s / s.cummax() - 1.0
    return {"x": [str(i) for i in dd.index], "y": [float(v) for v in dd.to_numpy()]}


def industry_exposure(run_id: str, trade_date: str | None = None) -> dict:
    """组合 vs 基准（域内等权）行业权重对比——验证行业中性效果。"""
    pos = store.load_positions(run_id)
    if pos.empty:
        return {"industries": [], "portfolio": [], "benchmark": []}
    d = str(trade_date) if trade_date else str(pos["trade_date"].max())
    cur = pos[pos["trade_date"].astype(str) == d]
    if cur.empty:
        return {"industries": [], "portfolio": [], "benchmark": []}

    ind = neutralize.industry_at([d])
    if ind.empty:
        return {"industries": [], "portfolio": [], "benchmark": []}
    imap = ind.drop_duplicates("ts_code").set_index("ts_code")["industry_code"]

    # 行业代码 → 中文名（前端显示更友好）
    im = storage.read_dim("industry_member")
    nmap: dict[str, str] = {}
    if not im.empty and "industry_name" in im.columns:
        sub = im[(im["src"] == config.SW_INDUSTRY_SRC) & (im["level"] == "L1")]
        nmap = dict(zip(sub["industry_code"], sub["industry_name"]))

    meta = store.load_meta(run_id) or {}
    pool = (meta.get("params") or {}).get("pool", config.UNIV_POOL)
    univ = universe.universe_frame([d], pool=pool)

    pw = cur.assign(ind=cur["ts_code"].map(imap)).dropna(subset=["ind"]) \
            .groupby("ind")["weight"].sum()
    bw = univ.assign(ind=univ["ts_code"].map(imap)).dropna(subset=["ind"]) \
             .groupby("ind").size()
    bw = bw / bw.sum() if bw.sum() else bw

    inds = sorted(set(pw.index) | set(bw.index))
    return {
        "date": d,
        "industries": [str(i) for i in inds],
        "names": [nmap.get(i, str(i)) for i in inds],
        "portfolio": [float(pw.get(i, 0.0)) for i in inds],
        "benchmark": [float(bw.get(i, 0.0)) for i in inds],
    }


def cost_breakdown(run_id: str) -> dict:
    """累计成本曲线 + 买卖方向拆解（换手高时这是最该看的图）。"""
    tr = store.load_trades(run_id)
    if tr.empty:
        return {"cum": {"x": [], "y": []}, "by_side": {}}
    tr = tr.copy()
    tr["date"] = tr["date"].astype(str)
    daily = tr.groupby("date")["cost"].sum().sort_index()
    cum = daily.cumsum()
    by_side = tr.groupby("side")["cost"].sum().to_dict()
    return {
        "cum": {"x": [str(i) for i in cum.index], "y": [float(v) for v in cum.to_numpy()]},
        "by_side": {k: float(v) for k, v in by_side.items()},
        "total": float(tr["cost"].sum()),
        "n_trades": int(len(tr)),
    }


def positions_detail(run_id: str, trade_date: str | None = None) -> dict:
    """某调仓日持仓明细：代码/名称/权重/行业。"""
    pos = store.load_positions(run_id)
    if pos.empty:
        return {"date": None, "rows": []}
    d = str(trade_date) if trade_date else str(pos["trade_date"].max())
    cur = pos[pos["trade_date"].astype(str) == d].copy()
    if cur.empty:
        return {"date": d, "rows": []}

    sb = storage.read_dim("stock_basic")[["ts_code", "name"]]
    cur = cur.merge(sb, on="ts_code", how="left")
    ind = neutralize.industry_at([d])
    if not ind.empty:
        cur = cur.merge(ind.drop_duplicates("ts_code")[["ts_code", "industry_code"]],
                        on="ts_code", how="left")
        im = storage.read_dim("industry_member")
        if not im.empty and "industry_name" in im.columns:
            sub = im[(im["src"] == config.SW_INDUSTRY_SRC) & (im["level"] == "L1")]
            nmap = dict(zip(sub["industry_code"], sub["industry_name"]))
            cur["industry_name"] = cur["industry_code"].map(nmap)
    cur = cur.sort_values("weight", ascending=False)
    return {"date": d, "rows": cur.replace({np.nan: None}).to_dict("records")}


def rebalance_dates_of(run_id: str) -> list[str]:
    pos = store.load_positions(run_id)
    return sorted(pos["trade_date"].astype(str).unique().tolist()) if not pos.empty else []
