"""Web 层配置。独立于 config.py，不修改既有配置。"""
from __future__ import annotations

import os

import config

# 回测结果落盘目录（新增，不影响既有 data/ 结构）
RUNS_DIR = config.DATA_DIR / "backtest"
# 策略实盘跟踪（纸上模拟）状态落盘目录
LIVE_DIR = config.DATA_DIR / "live"

HOST = "127.0.0.1"
PORT = 5000
CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]   # Vite 开发服务器

MAX_CONCURRENT_JOBS = 1        # 回测吃内存，串行执行
TRADES_PAGE_SIZE = 200
# 生产模式（run_web.py --prod）waitress 线程数：默认 4 足够单用户使用（前端一次
# 页面加载常并发发出数个请求），线程越多、glibc 分配的内存 arena 越多，内存较紧
# 张的部署（如小内存云服务器）可通过环境变量调小
WAITRESS_THREADS = int(os.environ.get("WAITRESS_THREADS", "4"))
