"""数据层相关端点：概览、校验结果、同步日志、个股。"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request

from quant_web.services import data_service

bp = Blueprint("data", __name__)


@bp.get("/health")
def health():
    return jsonify({"ok": True, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


@bp.get("/overview")
def overview():
    return jsonify(data_service.overview())


@bp.get("/industries")
def industries():
    return jsonify(data_service.list_industries())


@bp.get("/data/quality")
def quality():
    return jsonify(data_service.quality(int(request.args.get("limit", 500))))


@bp.get("/data/synclog")
def synclog():
    return jsonify(data_service.sync_log(int(request.args.get("limit", 500))))
