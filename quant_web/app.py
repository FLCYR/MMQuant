"""Flask 应用工厂。"""
from __future__ import annotations

from flask import Flask, jsonify
from flask_cors import CORS

from quant_web import webconfig
from quant_web.api import register_blueprints


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False          # 中文直出，不转义
    app.json.sort_keys = False
    CORS(app, origins=webconfig.CORS_ORIGINS)
    register_blueprints(app)

    @app.errorhandler(Exception)
    def on_error(e):
        code = getattr(e, "code", 500)
        return jsonify({"error": str(e), "type": type(e).__name__}), code

    @app.get("/")
    def index():
        return jsonify({"service": "quant_web", "api": "/api", "health": "/api/health"})

    return app


app = create_app()
