"""启动 Web API 服务。

    python scripts/run_web.py            # 开发模式（Flask 内置服务器）
    python scripts/run_web.py --prod     # 生产模式（waitress）
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse

from quant_web import webconfig
from quant_web.app import create_app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=webconfig.HOST)
    ap.add_argument("--port", type=int, default=webconfig.PORT)
    ap.add_argument("--prod", action="store_true", help="用 waitress 启动")
    args = ap.parse_args()

    app = create_app()
    print(f"API 服务：http://{args.host}:{args.port}/api/health")
    if args.prod:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=8)
    else:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
