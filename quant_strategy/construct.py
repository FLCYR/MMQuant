"""组合构建：综合得分 → 目标权重。

- top_n_equal        ：域内取得分最高 N 只等权（默认）。
- top_n_industry_neutral：按基准（域内）行业分布分配各行业权重，行业内取高分股等权，
  使组合行业暴露贴近基准，避免行业押注主导收益。
"""
from __future__ import annotations

import math

import pandas as pd


def top_n_equal(score: pd.Series, n: int, universe_codes) -> pd.Series:
    s = score[score.index.isin(set(universe_codes))].dropna()
    if s.empty:
        return pd.Series(dtype=float)
    picks = s.sort_values(ascending=False).head(n).index
    return pd.Series(1.0 / len(picks), index=picks, dtype=float)


def top_n_industry_neutral(score: pd.Series, n: int, universe_codes,
                           industry: pd.Series) -> pd.Series:
    """industry: index=ts_code -> industry_code（域内股票的行业归属）。

    行业目标权重 = 该行业在域内的股票占比（等权基准口径）；行业内按得分取前
    ceil(n * 行业权重) 只等权分摊该行业权重。
    """
    univ = set(universe_codes)
    s = score[score.index.isin(univ)].dropna()
    ind = industry[industry.index.isin(s.index)]
    if s.empty or ind.empty:
        return top_n_equal(score, n, universe_codes)

    df = pd.DataFrame({"score": s}).join(ind.rename("ind"), how="inner").dropna()
    if df.empty:
        return top_n_equal(score, n, universe_codes)

    ind_w = df["ind"].value_counts(normalize=True)          # 域内行业分布 = 基准行业权重
    out: dict[str, float] = {}
    for code, w in ind_w.items():
        k = max(1, math.ceil(n * float(w)))
        sub = df[df["ind"] == code].sort_values("score", ascending=False).head(k)
        if len(sub):
            for ts in sub.index:
                out[ts] = float(w) / len(sub)
    if not out:
        return top_n_equal(score, n, universe_codes)
    w = pd.Series(out, dtype=float)
    return w / w.sum()
