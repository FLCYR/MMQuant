"""单因子有效性评估：IC / 分组收益 / 多空组合 / IC 衰减 / 因子相关性。

评估口径：因子在调仓日 T 计算，前向收益为 T→下一调仓日（评估用收盘对收盘；
实盘通常再滞后一日买入）。评估域为 universe（默认中证500 可交易域）。
方向 direction：+1 越大越好，-1 越小越好；多空与判定按方向调整符号。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
from quant_factor import returns, universe
from quant_factor.factors import REGISTRY

_PERIODS = {"W": 52, "M": 12, "D": 252}


def periods_per_year(freq: str) -> int:
    return _PERIODS.get(freq, 52)


@dataclass
class FactorReport:
    name: str
    style: str
    direction: int
    n_periods: int
    ic_mean: float
    rankic_mean: float
    rankic_std: float
    rankic_ir: float
    rankic_t: float
    pos_ratio: float                 # RankIC 与 direction 同号的比例
    quantile_returns: list           # Q1..Qn 平均前向收益（按因子升序）
    ls_ann: float                    # 方向调整后多空年化
    ls_sharpe: float
    ls_maxdd: float
    ls_turnover: float
    decay: dict = field(default_factory=dict)   # {k: mean RankIC}

    def verdict(self) -> str:
        # 以 RankIC 幅度 + t 统计量（显著性）判定；周频 ICIR 天然偏低，仅作参考。
        a, t = abs(self.rankic_mean), abs(self.rankic_t)
        if a > 0.03 and t > 3:
            return "强"
        if a > 0.02 and t > 2:
            return "可用"
        return "无效/弱"


def _max_drawdown(nav: np.ndarray) -> float:
    peak = np.maximum.accumulate(nav)
    return float((nav / peak - 1.0).min())


def _rankic(a: pd.Series, b: pd.Series) -> float:
    """Spearman = 排名的 Pearson 相关（自实现，免 scipy 依赖）。"""
    return a.rank().corr(b.rank())


def evaluate_factor(name: str, processed: pd.DataFrame, rebal_dates: list[str],
                    pool: str = config.UNIV_POOL, freq: str = config.REBAL_FREQ,
                    quantiles: int = config.FACTOR_QUANTILES,
                    min_stocks: int = 20) -> FactorReport:
    fac = REGISTRY[name]
    direction = fac.direction
    ppy = periods_per_year(freq)

    # 因子值（处理后）∩ universe ∩ 前向收益
    f = processed[["trade_date", "ts_code", name]].dropna().rename(columns={name: "value"})
    univ = universe.universe_frame(rebal_dates, pool=pool)
    f = f.merge(univ, on=["trade_date", "ts_code"])
    fr = returns.forward_returns(rebal_dates, k=1)
    m = f.merge(fr, on=["trade_date", "ts_code"])

    ics, rics, ls_series, top_sets = [], [], [], {}
    q_accum = np.zeros(quantiles)
    q_count = 0
    for T, g in m.groupby("trade_date"):
        if len(g) < min_stocks:
            continue
        ics.append(g["value"].corr(g["fwd_ret"]))
        rics.append(_rankic(g["value"], g["fwd_ret"]))
        # 分组（按因子升序分 quantiles 组）
        q = pd.qcut(g["value"].rank(method="first"), quantiles, labels=False)
        gm = g.groupby(q)["fwd_ret"].mean()
        if len(gm) == quantiles:
            q_accum += gm.to_numpy()
            q_count += 1
            top = g[q == (quantiles - 1)]["ts_code"] if direction > 0 else g[q == 0]["ts_code"]
            bot = g[q == 0]["ts_code"] if direction > 0 else g[q == (quantiles - 1)]["ts_code"]
            ls = g[g["ts_code"].isin(top)]["fwd_ret"].mean() - g[g["ts_code"].isin(bot)]["fwd_ret"].mean()
            ls_series.append(ls)
            top_sets[T] = set(top)

    rics = np.array([x for x in rics if np.isfinite(x)])
    ics = np.array([x for x in ics if np.isfinite(x)])
    n = len(rics)
    rankic_mean = float(rics.mean()) if n else float("nan")
    rankic_std = float(rics.std(ddof=1)) if n > 1 else float("nan")
    rankic_ir = rankic_mean / rankic_std if rankic_std else float("nan")
    rankic_t = rankic_ir * np.sqrt(n) if n else float("nan")
    pos_ratio = float(np.mean(np.sign(rics) == np.sign(direction))) if n else float("nan")

    # 多空净值（top/bot 组已按 direction 选取，符号无需再调）
    ls = np.array(ls_series)
    nav = np.cumprod(1 + ls) if len(ls) else np.array([1.0])
    ls_ann = float(nav[-1] ** (ppy / max(len(ls), 1)) - 1) if len(ls) else float("nan")
    ls_sharpe = float(ls.mean() / ls.std(ddof=1) * np.sqrt(ppy)) if len(ls) > 1 and ls.std() else float("nan")
    ls_maxdd = _max_drawdown(nav)

    # 换手（top 组名单变动比例）
    ts_dates = sorted(top_sets)
    turns = []
    for i in range(1, len(ts_dates)):
        a, b = top_sets[ts_dates[i - 1]], top_sets[ts_dates[i]]
        if b:
            turns.append(len(b - a) / len(b))
    ls_turnover = float(np.mean(turns)) if turns else float("nan")

    # IC 衰减
    decay = {}
    for k in (1, 2, 4, 8):
        frk = returns.forward_returns(rebal_dates, k=k)
        mk = f.merge(frk, on=["trade_date", "ts_code"])
        rr = [_rankic(g["value"], g["fwd_ret"])
              for _, g in mk.groupby("trade_date") if len(g) >= min_stocks]
        rr = [x for x in rr if np.isfinite(x)]
        decay[k] = float(np.mean(rr)) if rr else float("nan")

    return FactorReport(
        name=name, style=fac.style, direction=direction, n_periods=n,
        ic_mean=float(ics.mean()) if len(ics) else float("nan"),
        rankic_mean=rankic_mean, rankic_std=rankic_std, rankic_ir=rankic_ir,
        rankic_t=rankic_t, pos_ratio=pos_ratio,
        quantile_returns=(q_accum / q_count).tolist() if q_count else [],
        ls_ann=ls_ann, ls_sharpe=ls_sharpe, ls_maxdd=ls_maxdd, ls_turnover=ls_turnover,
        decay=decay,
    )


def ic_frame(names: list[str], processed: pd.DataFrame, rebal_dates: list[str],
             pool: str = config.UNIV_POOL, min_stocks: int = 20) -> pd.DataFrame:
    """逐期 RankIC 表：index=调仓日 T，columns=因子名。

    第 T 期的 IC 需要 T→下一调仓日的收益，**只在下一调仓日才可知**；使用方
    （如 RollingICCombiner）必须只取 index < 当前日 的行，否则构成未来函数。
    """
    univ = universe.universe_frame(rebal_dates, pool=pool)
    fr = returns.forward_returns(rebal_dates, k=1)
    out: dict[str, dict] = {}
    for nm in names:
        if nm not in processed.columns:
            continue
        f = processed[["trade_date", "ts_code", nm]].dropna().rename(columns={nm: "value"})
        m = f.merge(univ, on=["trade_date", "ts_code"]).merge(fr, on=["trade_date", "ts_code"])
        col = {}
        for T, g in m.groupby("trade_date"):
            if len(g) >= min_stocks:
                v = _rankic(g["value"], g["fwd_ret"])
                if np.isfinite(v):
                    col[str(T)] = v
        out[nm] = col
    return pd.DataFrame(out).sort_index()


def factor_correlation(processed: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """因子间平均横截面相关（处理后值），用于识别冗余。"""
    cols = [n for n in names if n in processed.columns]
    mats = []
    for _, g in processed.groupby("trade_date"):
        c = g[cols].corr()
        mats.append(c.to_numpy())
    if not mats:
        return pd.DataFrame()
    avg = np.nanmean(np.stack(mats), axis=0)
    return pd.DataFrame(avg, index=cols, columns=cols)
