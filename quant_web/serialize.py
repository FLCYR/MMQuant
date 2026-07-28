"""DataFrame / Series → JSON 安全结构。

要点：NaN/Inf 转 None（JSON 不支持）、numpy 标量转原生类型、长序列可降采样。
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def clean(v: Any) -> Any:
    """单值清洗：NaN/Inf → None，numpy 标量 → 原生类型。"""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y%m%d")
    return v


def df_records(df: pd.DataFrame, limit: int | None = None) -> list[dict]:
    if df is None or df.empty:
        return []
    d = df.head(limit) if limit else df
    return [{k: clean(v) for k, v in rec.items()} for rec in d.to_dict("records")]


def series_xy(s: pd.Series, downsample: int | None = None) -> dict:
    """时间序列 → {x: [...], y: [...]}，可等间隔降采样以控制传输量。"""
    if s is None or len(s) == 0:
        return {"x": [], "y": []}
    if downsample and len(s) > downsample:
        step = math.ceil(len(s) / downsample)
        # 保留首尾，避免端点丢失
        idx = list(range(0, len(s), step))
        if idx[-1] != len(s) - 1:
            idx.append(len(s) - 1)
        s = s.iloc[idx]
    return {"x": [str(i) for i in s.index], "y": [clean(v) for v in s.to_numpy()]}


def dataclass_dict(obj) -> dict:
    """dataclass → 清洗后的 dict（用于 Performance 等）。"""
    src = obj.__dict__ if hasattr(obj, "__dict__") else dict(obj)
    out = {}
    for k, v in src.items():
        if isinstance(v, dict):
            out[k] = {kk: clean(vv) for kk, vv in v.items()}
        else:
            out[k] = clean(v)
    return out
