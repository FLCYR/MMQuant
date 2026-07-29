"""Web 层配置。独立于 config.py，不修改既有配置。"""
from __future__ import annotations

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
