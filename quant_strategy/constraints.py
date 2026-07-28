"""权重约束（可组合的后处理函数）。"""
from __future__ import annotations

import pandas as pd


def normalize(w: pd.Series) -> pd.Series:
    tot = w.sum()
    return w / tot if tot > 0 else w


def cap_weight(w: pd.Series, max_weight: float, iters: int = 10) -> pd.Series:
    """个股权重上限；超限部分按比例分配给未超限个股，迭代收敛。"""
    if w.empty or max_weight <= 0:
        return w
    x = normalize(w.copy())
    for _ in range(iters):
        over = x > max_weight
        if not over.any():
            break
        excess = (x[over] - max_weight).sum()
        x[over] = max_weight
        room = x[~over]
        if room.empty or room.sum() <= 0:
            break
        x[~over] = room + excess * room / room.sum()
    return x


def apply_all(w: pd.Series, max_weight: float | None = None) -> pd.Series:
    w = normalize(w)
    if max_weight:
        w = cap_weight(w, max_weight)
    return w
