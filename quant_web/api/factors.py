"""因子层端点：因子列表、评估结果（带缓存）、触发重算。"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

import config
from quant_web import jobs
from quant_web.services import factor_service

bp = Blueprint("factors", __name__)


def _parse_pool(raw):
    """pool 可为字符串简写或 JSON 编码的 dict 组合。"""
    if raw is None:
        return config.UNIV_POOL
    try:
        return json.loads(raw)          # '"csi500"' / '{"then":[...]}' → 原生值
    except (ValueError, TypeError):
        return raw                       # 裸字符串 'csi500'


def _params_from_query():
    factors = request.args.getlist("factors") or None
    return {
        "factors": factors or [f["name"] for f in factor_service.list_factors()],
        "start": request.args.get("start", "20160101"),
        "end": request.args.get("end") or None,
        "freq": request.args.get("freq", config.REBAL_FREQ),
        "pool": _parse_pool(request.args.get("pool")),
    }


@bp.get("/factors")
def list_factors():
    return jsonify(factor_service.list_factors())


@bp.get("/factors/evaluation")
def get_evaluation():
    p = _params_from_query()
    cached = factor_service.get_cached(p["factors"], p["start"], p["end"], p["freq"], p["pool"])
    if cached is None:
        return jsonify({"cached": False, "message": "尚无缓存，请 POST 触发计算"}), 404
    return jsonify({"cached": True, **cached})


@bp.post("/factors/evaluation")
def run_evaluation():
    p = {**_params_from_query(), **(request.get_json(silent=True) or {})}
    job_id = jobs.submit(factor_service.evaluate_all, p)
    return jsonify({"job_id": job_id}), 202
