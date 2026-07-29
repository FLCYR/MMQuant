"""策略实盘跟踪（纸上模拟）端点：创建/列表/详情/停止/删除，手动触发推进。

只做信号生成 + 虚拟记账，不接触任何真实资金/账户/券商 API。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from quant_web import jobs
from quant_web.services import live_service

bp = Blueprint("live", __name__)


@bp.get("/live/strategies")
def list_strategies():
    return jsonify(live_service.list_live())


@bp.post("/live/strategies")
def create_strategy():
    params = request.get_json(silent=True) or {}
    job_id = jobs.submit(live_service.create, params)
    return jsonify({"job_id": job_id}), 202


@bp.get("/live/strategies/<run_id>")
def get_strategy(run_id: str):
    d = live_service.detail(run_id)
    return (jsonify(d), 200) if d else (jsonify({"error": "not found"}), 404)


@bp.post("/live/strategies/<run_id>/stop")
def stop_strategy(run_id: str):
    return jsonify({"stopped": live_service.stop(run_id)})


@bp.delete("/live/strategies/<run_id>")
def delete_strategy(run_id: str):
    return jsonify({"deleted": live_service.delete(run_id)})


@bp.post("/live/strategies/<run_id>/advance")
def advance_strategy(run_id: str):
    """手动立即推进单个实例（测试/补跑用），异步任务。"""
    job_id = jobs.submit(lambda job_id_: live_service.advance(run_id))
    return jsonify({"job_id": job_id}), 202
