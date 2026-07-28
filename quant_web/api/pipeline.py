"""数据管道端点：日度增量 / 回补 / 校验 / 建因子面板（均异步 job）。

写操作一律 POST 返回 job_id，前端轮询 /api/jobs/{id} 看进度与结果。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from quant_web import jobs
from quant_web.services import pipeline_service

bp = Blueprint("pipeline", __name__)


@bp.get("/pipeline/info")
def info():
    return jsonify(pipeline_service.info())


@bp.post("/pipeline/daily")
def daily():
    params = request.get_json(silent=True) or {}
    return jsonify({"job_id": jobs.submit(pipeline_service.daily_update, params)}), 202


@bp.post("/pipeline/backfill")
def backfill():
    params = request.get_json(silent=True) or {}
    return jsonify({"job_id": jobs.submit(pipeline_service.backfill, params)}), 202


@bp.post("/pipeline/checks")
def checks():
    params = request.get_json(silent=True) or {}
    return jsonify({"job_id": jobs.submit(pipeline_service.run_checks, params)}), 202


@bp.post("/pipeline/build_factors")
def build_factors():
    params = request.get_json(silent=True) or {}
    return jsonify({"job_id": jobs.submit(pipeline_service.build_factors, params)}), 202
