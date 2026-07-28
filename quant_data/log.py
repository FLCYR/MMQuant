"""统一日志：同时输出到控制台和 data/logs/quant.log。"""
from __future__ import annotations

import logging
import sys


def get_logger(name: str = "quant_data") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    try:
        import config
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(config.LOG_DIR / "quant.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass
    return logger
