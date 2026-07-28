"""具体可插拔池：全市场 / 指数成分 / 市值 / 流动性 / 行业 / 板块 / 自定义名单。

每个池 `apply(upstream)` 在上游 [trade_date, ts_code] 集合之上筛选，输出同结构子集。
新增池只需实现 apply + @register_pool，无需改动解析与调用点。

PIT 安全：指数/行业走区间成员表（in_date<=T<out_date），市值/流动性用当日横截面，
板块/自定义为静态属性（不随日期变化），均不引入未来信息。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config
from quant_data import storage
from quant_factor.universe.base import KEYS, Pool, register_pool


# --------------------------------------------------------------- 通用：横截面排序筛选
def _rank_select(m: pd.DataFrame, col: str, bottom, top, q) -> pd.DataFrame:
    """按 col 在每个交易日横截面排序，取最小/最大 K 只或分位带（升序：小值在前）。"""
    picks = []
    for _, g in m.groupby("trade_date"):
        g = g.sort_values(col)
        if bottom is not None:
            sel = g.head(int(bottom))
        elif top is not None:
            sel = g.tail(int(top))
        elif q is not None:
            lo, hi = q
            r = g[col].rank(pct=True, method="first")
            sel = g[(r > lo) & (r <= hi)] if lo > 0 else g[r <= hi]
        else:
            sel = g
        picks.append(sel[KEYS])
    return pd.concat(picks, ignore_index=True) if picks else m.iloc[0:0][KEYS]


# --------------------------------------------------------------- 全市场（恒等）
@dataclass
class AllPool:
    """不再筛选，直接返回基础池。"""

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        return up


# --------------------------------------------------------------- 指数成分（区间）
@dataclass
class IndexPool:
    """指数历史成分（区间表 dim_index_member，PIT：in_date<=T<out_date）。"""

    index_code: str

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        if up.empty:
            return up
        im = storage.read_dim("index_member")
        if im.empty:
            return up.iloc[0:0]
        im = im[im["index_code"] == self.index_code].copy()
        if im.empty:
            raise ValueError(
                f"index_member 无 {self.index_code} 的成分记录，请先回补（backfill p5）")
        im["in_date"] = im["in_date"].astype(str)
        im["of"] = im["out_date"].fillna("99991231").astype(str)
        rows = []
        for T in up["trade_date"].astype(str).unique():
            for c in im[(im["in_date"] <= T) & (im["of"] > T)]["ts_code"]:
                rows.append((T, c))
        mem = pd.DataFrame(rows, columns=KEYS)
        return up.merge(mem, on=KEYS)


# --------------------------------------------------------------- 市值排序/过滤
@dataclass
class SizePool:
    """按当日总市值 total_mv 在上游内排序筛选（PIT：用当日 daily_basic 横截面）。

    三选一：bottom=K 取最小 K 只（小盘）/ top=K 取最大 K 只（大盘）/
            q=[lo,hi] 取市值分位落在 (lo,hi] 的（0=最小、1=最大）。
    """

    bottom: int | None = None
    top: int | None = None
    q: tuple[float, float] | None = None

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        if up.empty:
            return up
        db = storage.fact_source("daily_basic")
        if not db:
            raise RuntimeError("daily_basic 无数据，SizePool 不可用")
        dstr = ",".join(f"'{d}'" for d in up["trade_date"].astype(str).unique())
        mv = storage.query(f"""SELECT ts_code, CAST(trade_date AS VARCHAR) trade_date,
            total_mv FROM {db} WHERE trade_date IN ({dstr}) AND total_mv > 0""")
        m = up.merge(mv, on=KEYS)
        return _rank_select(m, "total_mv", self.bottom, self.top, self.q) if not m.empty else up.iloc[0:0]


# --------------------------------------------------------------- 流动性排序/过滤
@dataclass
class LiquidityPool:
    """按流动性在上游内排序筛选。by='amount' 成交额（daily_quote）/ 'turnover' 换手率（daily_basic）。

    top=K 取最高 K 只（最活跃）/ bottom=K 取最低 K 只 / q=[lo,hi] 分位带（1=最活跃）。
    用当日横截面，PIT 安全。默认按成交额取最活跃。
    """

    bottom: int | None = None
    top: int | None = None
    q: tuple[float, float] | None = None
    by: str = "amount"

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        if up.empty:
            return up
        dstr = ",".join(f"'{d}'" for d in up["trade_date"].astype(str).unique())
        if self.by == "amount":
            src = storage.fact_source("daily_quote")
            metric = storage.query(f"""SELECT ts_code, CAST(trade_date AS VARCHAR) trade_date,
                amount AS liq FROM {src} WHERE trade_date IN ({dstr})
                AND is_suspended = 0 AND amount > 0""")
        elif self.by == "turnover":
            src = storage.fact_source("daily_basic")
            metric = storage.query(f"""SELECT ts_code, CAST(trade_date AS VARCHAR) trade_date,
                turnover_rate AS liq FROM {src} WHERE trade_date IN ({dstr})
                AND turnover_rate IS NOT NULL""")
        else:
            raise ValueError(f"LiquidityPool.by 只支持 amount / turnover，得到 {self.by!r}")
        m = up.merge(metric, on=KEYS)
        return _rank_select(m, "liq", self.bottom, self.top, self.q) if not m.empty else up.iloc[0:0]


# --------------------------------------------------------------- 行业子集（区间）
@dataclass
class IndustryPool:
    """SW 行业子集（区间表 dim_industry_member，PIT）。codes=行业代码列表，level 默认 L1。"""

    codes: list[str]
    level: str = "L1"

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        if up.empty:
            return up
        im = storage.read_dim("industry_member")
        if im.empty:
            return up.iloc[0:0]
        im = im[(im["src"] == config.SW_INDUSTRY_SRC) & (im["level"] == self.level)
                & (im["industry_code"].isin(self.codes))].copy()
        if im.empty:
            raise ValueError(f"industry_member 无 {self.level} 行业 {self.codes} 的记录")
        im["in_date"] = im["in_date"].astype(str)
        im["of"] = im["out_date"].fillna("99991231").astype(str)
        rows = []
        for T in up["trade_date"].astype(str).unique():
            for c in im[(im["in_date"] <= T) & (im["of"] > T)]["ts_code"].unique():
                rows.append((T, c))
        mem = pd.DataFrame(rows, columns=KEYS)
        return up.merge(mem, on=KEYS)


# --------------------------------------------------------------- 板块（静态属性）
@dataclass
class BoardPool:
    """板块筛选（stock_basic.market：主板/创业板/科创板/北交所）。与日期无关。"""

    markets: list[str]

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        if up.empty:
            return up
        sb = storage.read_dim("stock_basic")
        if sb.empty or "market" not in sb.columns:
            raise RuntimeError("stock_basic 缺 market 字段，BoardPool 不可用")
        valid = set(sb["market"].dropna().unique())
        unknown = [m for m in self.markets if m not in valid]
        if unknown:
            raise ValueError(f"未知板块 {unknown}，可选：{sorted(valid)}")
        codes = set(sb[sb["market"].isin(self.markets)]["ts_code"])
        return up[up["ts_code"].isin(codes)].reset_index(drop=True)


# --------------------------------------------------------------- 自定义名单（静态）
@dataclass
class CustomPool:
    """静态白名单：只保留给定 ts_code。与日期无关，供手工池 / 外部选股结果接入。"""

    codes: list[str] = field(default_factory=list)

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        if up.empty:
            return up
        keep = set(self.codes)
        return up[up["ts_code"].isin(keep)].reset_index(drop=True)


# --------------------------------------------------------------- 注册
def _as_list(v) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    if isinstance(v, dict):
        return list(v.get("codes") or v.get("markets") or [])
    return list(v or [])


@register_pool("all")
def _all(val) -> Pool:
    return AllPool()


@register_pool("index")
def _index(val) -> Pool:
    return IndexPool(str(val))


@register_pool("size")
def _size(val) -> Pool:
    v = val or {}
    return SizePool(bottom=v.get("bottom"), top=v.get("top"),
                    q=tuple(v["q"]) if v.get("q") else None)


@register_pool("liquidity")
def _liquidity(val) -> Pool:
    v = val or {}
    return LiquidityPool(bottom=v.get("bottom"), top=v.get("top"),
                         q=tuple(v["q"]) if v.get("q") else None,
                         by=v.get("by", "amount"))


@register_pool("industry")
def _industry(val) -> Pool:
    level = val.get("level", "L1") if isinstance(val, dict) else "L1"
    return IndustryPool(_as_list(val), level)


@register_pool("board")
def _board(val) -> Pool:
    return BoardPool(_as_list(val))


@register_pool("custom")
def _custom(val) -> Pool:
    return CustomPool(_as_list(val))
