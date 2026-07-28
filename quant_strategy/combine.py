"""多因子合成器（可插拔）。

因子方向统一：把每个因子乘以 `REGISTRY[name].direction`，使"越大越好"。

- EqualWeightCombiner：方向调整后的 z-score 等权平均。无参数、不过拟合，默认选择。
- RollingICCombiner  ：按历史 RankIC 均值加权。**防未来关键**：第 t 期 IC 需要
  t→t+1 的收益，只在 t+1 可知；故 T 时点只取 `index < T` 的 IC 历史。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant_factor.factors import REGISTRY


def _directional(panel: pd.DataFrame, T: str, names: list[str]) -> pd.DataFrame:
    """取 T 期横截面，按方向调整符号，返回 index=ts_code 的因子矩阵。"""
    g = panel[panel["trade_date"] == str(T)]
    if g.empty:
        return pd.DataFrame()
    g = g.set_index("ts_code")
    cols = [n for n in names if n in g.columns]
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame({n: g[n].astype(float) * REGISTRY[n].direction for n in cols})


def _weighted_mean(z: pd.DataFrame, w: pd.Series, min_valid: int) -> pd.Series:
    """按权重求可用因子的加权均值（自动处理缺失，不因缺因子而系统性低估）。"""
    num = z.mul(w, axis=1).sum(axis=1, min_count=1)
    den = z.notna().mul(w, axis=1).sum(axis=1)
    s = num / den.replace(0.0, np.nan)
    return s[z.notna().sum(axis=1) >= min_valid].dropna()


class EqualWeightCombiner:
    def __init__(self, min_valid_ratio: float = 0.5):
        self.min_valid_ratio = min_valid_ratio

    def score(self, T: str, panel: pd.DataFrame, names: list[str]) -> pd.Series:
        z = _directional(panel, T, names)
        if z.empty:
            return pd.Series(dtype=float)
        w = pd.Series(1.0, index=z.columns)
        need = max(1, int(len(z.columns) * self.min_valid_ratio))
        return _weighted_mean(z, w, need)


class RollingICCombiner:
    """按历史 RankIC 动态加权；历史不足时回退等权。

    ic_df 由 `quant_factor.evaluate.ic_frame` 生成（index=调仓日, columns=因子, 原始符号）。
    """

    def __init__(self, ic_df: pd.DataFrame, min_periods: int = 26,
                 clip_negative: bool = True, min_valid_ratio: float = 0.5):
        self.ic_df = ic_df.copy()
        self.ic_df.index = [str(i) for i in self.ic_df.index]
        self.min_periods = min_periods
        self.clip_negative = clip_negative
        self.min_valid_ratio = min_valid_ratio
        self._fallback = EqualWeightCombiner(min_valid_ratio)

    def weights_at(self, T: str, names: list[str]) -> pd.Series | None:
        hist = self.ic_df[self.ic_df.index < str(T)]      # 严格早于 T：无未来
        if len(hist) < self.min_periods:
            return None
        w = {}
        for n in names:
            if n not in hist.columns:
                continue
            m = hist[n].mean() * REGISTRY[n].direction     # 方向调整后应为正
            if not np.isfinite(m):
                continue
            w[n] = max(m, 0.0) if self.clip_negative else m
        tot = sum(abs(v) for v in w.values())
        if not w or tot <= 0:
            return None
        return pd.Series({k: v / tot for k, v in w.items()})

    def score(self, T: str, panel: pd.DataFrame, names: list[str]) -> pd.Series:
        w = self.weights_at(T, names)
        if w is None:
            return self._fallback.score(T, panel, names)
        z = _directional(panel, T, [n for n in names if n in w.index])
        if z.empty:
            return pd.Series(dtype=float)
        w = w.reindex(z.columns).fillna(0.0)
        need = max(1, int(len(z.columns) * self.min_valid_ratio))
        return _weighted_mean(z, w, need)
