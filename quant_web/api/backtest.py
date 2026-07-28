"""回测端点：发起（异步）、列表、明细、净值/回撤/滚动超额/持仓/交易/行业暴露/成本。"""
from __future__ import annotations

import math

from flask import Blueprint, jsonify, request

from quant_web import jobs, store, webconfig
from quant_web.serialize import clean, df_records, series_xy
from quant_web.services import backtest_service

bp = Blueprint("backtest", __name__)


@bp.get("/backtest/defaults")
def defaults():
    return jsonify({"params": backtest_service.default_params(),
                    "factors": backtest_service.available_factors(),
                    "industries": backtest_service.available_industries(),
                    "strategies": backtest_service.available_strategies()})


@bp.get("/backtest/runs")
def list_runs():
    return jsonify(store.list_runs())


@bp.post("/backtest/runs")
def create_run():
    params = request.get_json(silent=True) or {}
    job_id = jobs.submit(backtest_service.run_and_save, params)
    return jsonify({"job_id": job_id}), 202


@bp.get("/backtest/runs/<run_id>")
def get_run(run_id: str):
    meta = store.load_meta(run_id)
    return (jsonify(meta), 200) if meta else (jsonify({"error": "not found"}), 404)


@bp.delete("/backtest/runs/<run_id>")
def delete_run(run_id: str):
    return jsonify({"deleted": store.delete_run(run_id)})


@bp.get("/backtest/runs/<run_id>/nav")
def nav(run_id: str):
    df = store.load_nav(run_id)
    if df.empty:
        return jsonify({"error": "not found"}), 404
    ds = int(request.args.get("downsample", 0)) or None
    if ds and len(df) > ds:                       # 等间隔降采样，保留首尾
        step = math.ceil(len(df) / ds)
        idx = list(range(0, len(df), step))
        if idx[-1] != len(df) - 1:
            idx.append(len(df) - 1)
        df = df.iloc[idx]
    out = {"x": [str(i) for i in df.index]}
    for c in df.columns:
        out[c] = [clean(v) for v in df[c].to_numpy()]
    return jsonify(out)


@bp.get("/backtest/runs/<run_id>/drawdown")
def drawdown(run_id: str):
    return jsonify(backtest_service.drawdown(run_id, request.args.get("col", "nav")))


@bp.get("/backtest/runs/<run_id>/rolling_excess")
def rolling_excess(run_id: str):
    return jsonify(backtest_service.rolling_excess(run_id, int(request.args.get("window", 252))))


@bp.get("/backtest/runs/<run_id>/turnover")
def turnover(run_id: str):
    df = store.load_turnover(run_id)
    if df.empty:
        return jsonify({"x": [], "y": []})
    return jsonify(series_xy(df["turnover"]))


@bp.get("/backtest/runs/<run_id>/dates")
def dates(run_id: str):
    return jsonify(backtest_service.rebalance_dates_of(run_id))


@bp.get("/backtest/runs/<run_id>/positions")
def positions(run_id: str):
    return jsonify(backtest_service.positions_detail(run_id, request.args.get("date")))


@bp.get("/backtest/runs/<run_id>/exposure")
def exposure(run_id: str):
    return jsonify(backtest_service.industry_exposure(run_id, request.args.get("date")))


@bp.get("/backtest/runs/<run_id>/cost")
def cost(run_id: str):
    return jsonify(backtest_service.cost_breakdown(run_id))


@bp.get("/backtest/runs/<run_id>/trades")
def trades(run_id: str):
    df = store.load_trades(run_id)
    if df.empty:
        return jsonify({"total": 0, "page": 1, "rows": []})
    side = request.args.get("side")
    code = request.args.get("ts_code")
    if side:
        df = df[df["side"] == side]
    if code:
        df = df[df["ts_code"].str.contains(code, case=False, na=False)]
    page = max(1, int(request.args.get("page", 1)))
    size = min(1000, int(request.args.get("size", webconfig.TRADES_PAGE_SIZE)))
    total = len(df)
    df = df.sort_values("date", ascending=False).iloc[(page - 1) * size: page * size]
    return jsonify({"total": total, "page": page, "size": size, "rows": df_records(df)})
