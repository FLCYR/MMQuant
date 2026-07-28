"""多因子选股策略：合成器 + 构建器 + 约束 的组装。

`target_weights(T)` 只使用 ≤T 的信息（因子面板在 T 计算、universe 在 T 判定），
成交由引擎安排在 T 的下一交易日开盘，结构上无前视。
"""
from __future__ import annotations

import pandas as pd

import config
from quant_factor import compute, evaluate, neutralize, universe
from quant_strategy import constraints, construct
from quant_strategy.base import StrategyContext, register_strategy
from quant_strategy.combine import EqualWeightCombiner, RollingICCombiner


class MultiFactorStrategy:
    def __init__(self, panel: pd.DataFrame, rebal_dates: list[str],
                 combiner=None, names: list[str] | None = None,
                 n: int = config.STRATEGY_TOPN, pool: str = config.UNIV_POOL,
                 industry_neutral: bool = False, max_weight: float | None = None):
        self.combiner = combiner or EqualWeightCombiner()
        self.names = list(names or config.STRATEGY_FACTORS)
        self.n = n
        self.industry_neutral = industry_neutral
        self.max_weight = max_weight

        rebal_dates = [str(d) for d in rebal_dates]
        # 因子面板按日预分组（避免每次调仓全表扫描）
        sub = panel[panel["trade_date"].astype(str).isin(set(rebal_dates))]
        self._panel_by_date = {str(T): g for T, g in sub.groupby("trade_date")}

        # 可投域预取
        uf = universe.universe_frame(rebal_dates, pool=pool)
        self._univ = {str(T): set(g["ts_code"]) for T, g in uf.groupby("trade_date")}

        # 行业（仅行业中性时需要）
        self._ind: dict[str, pd.Series] = {}
        if industry_neutral:
            ind = neutralize.industry_at(rebal_dates)
            self._ind = {str(T): g.set_index("ts_code")["industry_code"]
                         for T, g in ind.groupby("trade_date")}

        # target_weights(T) 是 T 的纯函数，缓存避免含成本/零成本两次回测重复计算
        self._wcache: dict[str, pd.Series] = {}

    # ------------------------------------------------------------------
    def target_weights(self, T: str) -> pd.Series:
        T = str(T)
        if T not in self._wcache:
            self._wcache[T] = self._compute_weights(T)
        return self._wcache[T]

    def _compute_weights(self, T: str) -> pd.Series:
        univ = self._univ.get(T)
        g = self._panel_by_date.get(T)
        if not univ or g is None or g.empty:
            return pd.Series(dtype=float)

        score = self.combiner.score(T, g, self.names)
        if score.empty:
            return pd.Series(dtype=float)

        if self.industry_neutral and T in self._ind:
            w = construct.top_n_industry_neutral(score, self.n, univ, self._ind[T])
        else:
            w = construct.top_n_equal(score, self.n, univ)
        return constraints.apply_all(w, self.max_weight)


# --------------------------------------------------------------- 注册为可选策略
# type 取值供前端通用表单渲染："factors"(因子多选，选项来自 REGISTRY)/"int"/"float"(可为空)/
# "bool"/"select"(需 options)。新增策略只需照此写一份 schema，无需改前端代码。
PARAM_SCHEMA = [
    {"key": "factors", "type": "factors", "label": "因子",
     "default": list(config.STRATEGY_FACTORS)},
    {"key": "topn", "type": "int", "label": "持股数", "default": config.STRATEGY_TOPN},
    {"key": "combiner", "type": "select", "label": "合成器", "default": "equal",
     "options": [{"value": "equal", "label": "等权 z-score"},
                {"value": "ic", "label": "滚动 IC 加权"}]},
    {"key": "industry_neutral", "type": "bool", "label": "行业中性", "default": False},
    {"key": "max_weight", "type": "float", "label": "个股权重上限", "default": None},
]


@register_strategy("multifactor", "多因子选股",
                   "多因子合成打分，域内取 top-N 等权（可选行业中性）", PARAM_SCHEMA)
def _build(params: dict, ctx: StrategyContext) -> MultiFactorStrategy:
    ctx.progress("读取因子面板", 15)
    panel = ctx.get_panel()
    if panel.empty:
        raise RuntimeError("因子面板为空，请先运行 scripts/build_factors.py 或页面「重建因子面板」")
    compute.check_dates_coverage(ctx.dates, panel)

    names = params["factors"]
    if params.get("combiner") == "ic":
        ctx.progress("计算历史 IC（仅用过去）", 25)
        ic = evaluate.ic_frame(names, panel, ctx.dates, pool=ctx.pool)
        combiner = RollingICCombiner(ic)
    else:
        combiner = EqualWeightCombiner()

    return MultiFactorStrategy(panel, ctx.dates, combiner, names, int(params["topn"]),
                               ctx.pool, bool(params["industry_neutral"]), params.get("max_weight"))
