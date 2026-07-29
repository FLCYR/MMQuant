"""Flask 蓝图注册。路由层保持很薄，逻辑都在 services/。"""
from __future__ import annotations


def register_blueprints(app):
    from quant_web.api.backtest import bp as backtest_bp
    from quant_web.api.data import bp as data_bp
    from quant_web.api.factors import bp as factors_bp
    from quant_web.api.jobs import bp as jobs_bp
    from quant_web.api.live import bp as live_bp
    from quant_web.api.pipeline import bp as pipeline_bp

    for bp in (data_bp, factors_bp, backtest_bp, jobs_bp, pipeline_bp, live_bp):
        app.register_blueprint(bp, url_prefix="/api")
