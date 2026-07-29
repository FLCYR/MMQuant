"""异步任务状态端点（前端轮询用）。"""
from __future__ import annotations

from flask import Blueprint, jsonify

from quant_web import jobs as jobmod

bp = Blueprint("jobs", __name__)


@bp.get("/jobs/<job_id>")
def get_job(job_id: str):
    j = jobmod.get(job_id)
    return (jsonify(j), 200) if j else (jsonify({"error": "not found"}), 404)
