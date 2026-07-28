"""因子处理：去极值(MAD) → 行业+市值中性化(OLS 残差) → 标准化(zscore)。

中性化在每个横截面上，对 [行业哑变量(SW L1) + 标准化 ln市值] 做 OLS，取残差；
消除因子中的行业与规模暴露，避免多因子叠加时被单一风格主导。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from quant_data import storage


# --------------------------------------------------------------- 基础变换
def winsor_mad(s: pd.Series, k: float = config.WINSOR_MAD) -> pd.Series:
    med = s.median()
    mad = (s - med).abs().median()
    if not np.isfinite(mad) or mad == 0:
        lo, hi = s.quantile(0.01), s.quantile(0.99)
    else:
        f = 1.4826 * mad
        lo, hi = med - k * f, med + k * f
    return s.clip(lo, hi)


def zscore(s: pd.Series) -> pd.Series:
    sd = s.std()
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / sd


# --------------------------------------------------------------- 中性化输入
def lnmktcap_at(dates: list[str]) -> pd.DataFrame:
    db = storage.fact_source("daily_basic")
    dstr = ",".join(f"'{d}'" for d in dates)
    return storage.query(f"""SELECT ts_code, CAST(trade_date AS VARCHAR) trade_date,
        ln(total_mv) AS lnmktcap FROM {db}
        WHERE trade_date IN ({dstr}) AND total_mv>0""")


def industry_at(dates: list[str]) -> pd.DataFrame:
    """每调仓日各股 SW L1 行业（区间 PIT，一股一行业）。"""
    ind = storage.read_dim("industry_member")
    ind = ind[(ind["src"] == config.SW_INDUSTRY_SRC) & (ind["level"] == "L1")].copy()
    ind["in_date"] = ind["in_date"].astype(str)
    ind["of"] = ind["out_date"].fillna("99991231").astype(str)
    frames = []
    for T in dates:
        a = ind[(ind["in_date"] <= T) & (ind["of"] > T)][["ts_code", "industry_code"]]
        a = a.drop_duplicates("ts_code").assign(trade_date=str(T))
        frames.append(a)
    if not frames:
        return pd.DataFrame(columns=["trade_date", "ts_code", "industry_code"])
    return pd.concat(frames, ignore_index=True)[["trade_date", "ts_code", "industry_code"]]


# --------------------------------------------------------------- 横截面中性化
def _neutralize_one(y: pd.Series, ind: pd.Series, size: pd.Series) -> pd.Series:
    """单横截面：去极值→对(行业哑变量+ln市值)回归取残差→zscore。返回按 y.index 对齐。"""
    d = pd.DataFrame({"y": y, "ind": ind, "size": size})
    valid = d["y"].notna() & d["size"].notna()
    d2 = d[valid]
    if len(d2) < 10:
        return zscore(d["y"])
    yv = winsor_mad(d2["y"]).to_numpy(dtype=float)
    sz = zscore(d2["size"]).to_numpy(dtype=float).reshape(-1, 1)
    dum = pd.get_dummies(d2["ind"].fillna("NA"), drop_first=True).to_numpy(dtype=float)
    X = np.column_stack([np.ones(len(d2)), sz, dum]) if dum.size else np.column_stack([np.ones(len(d2)), sz])
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = pd.Series(yv - X @ beta, index=d2.index)
    return zscore(resid).reindex(y.index)


def neutralize_factor(factor_long: pd.DataFrame, industry_long: pd.DataFrame,
                      lnmktcap_long: pd.DataFrame) -> pd.DataFrame:
    """长表因子 [trade_date,ts_code,value] → 处理后 [trade_date,ts_code,value]。"""
    if factor_long.empty:
        return factor_long
    m = (factor_long
         .merge(industry_long, on=["trade_date", "ts_code"], how="left")
         .merge(lnmktcap_long, on=["trade_date", "ts_code"], how="left"))
    outs = []
    for T, g in m.groupby("trade_date"):
        z = _neutralize_one(g["value"].reset_index(drop=True),
                            g["industry_code"].reset_index(drop=True),
                            g["lnmktcap"].reset_index(drop=True))
        outs.append(pd.DataFrame({"trade_date": T, "ts_code": g["ts_code"].to_numpy(),
                                  "value": z.to_numpy()}))
    return pd.concat(outs, ignore_index=True)


def standardize_only(factor_long: pd.DataFrame) -> pd.DataFrame:
    """仅去极值+zscore（不中性化），用于对照。"""
    if factor_long.empty:
        return factor_long
    outs = []
    for T, g in factor_long.groupby("trade_date"):
        z = zscore(winsor_mad(g["value"]))
        outs.append(pd.DataFrame({"trade_date": T, "ts_code": g["ts_code"].to_numpy(),
                                  "value": z.to_numpy()}))
    return pd.concat(outs, ignore_index=True)
