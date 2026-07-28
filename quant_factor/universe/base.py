"""股票池核心：基础池 + 可插拔池的协议、注册表与解析。

两层分离（见设计）：
- **基础池 BasePool**：恒定的可投性硬约束（有行情∧非停牌∧非ST∧上市满N日），
  永远最先执行，产出"当日干净可投集合"（root frame）。
- **可插拔池 Pool**：在上游集合之上再筛/再排序/再组合，实现 `apply(upstream)->frame`。

`universe_frame(dates, pool)` = resolve_pool(pool).apply(BasePool.members(dates))。
`pool` 是可解析的 spec：字符串简写 / dict 组合 / Pool 对象，统一经 resolve_pool 落地。

所有池必须 PIT 安全：只使用 ≤ 当日 的信息（区间成员表、当日横截面），杜绝未来函数。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

import pandas as pd

import config
from quant_data import storage, calendar

KEYS = ["trade_date", "ts_code"]

# 广基指数别名 → 指数代码（字符串简写用）
ALIASES = {
    "csi500": "000905.SH",
    "hs300": "000300.SH",
    "csi1000": "000852.SH",
    "sz50": "000016.SH",
}


# --------------------------------------------------------------- 协议
@runtime_checkable
class Pool(Protocol):
    """可插拔池：在上游 [trade_date, ts_code] 集合之上做筛选，返回同结构子集。"""

    def apply(self, upstream: pd.DataFrame) -> pd.DataFrame:
        ...


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=KEYS)


# --------------------------------------------------------------- 基础池
@dataclass
class BasePool:
    """恒定可投性硬约束，产出每个调仓日的干净可投集合（root frame）。

    等价于原 universe_frame 的步骤 1、2：当日可交易（非ST/非停牌/有行情）∩ 上市满 N 交易日。
    可交易性中的**涨跌停**属执行层（见 quant_backtest.market），不在此处，避免双重口径。
    """

    min_list_days: int = config.UNIV_MIN_LIST_DAYS
    drop_st: bool = True
    drop_suspended: bool = True

    def members(self, dates: list[str]) -> pd.DataFrame:
        src = storage.fact_source("daily_quote")
        if not src or not dates:
            return _empty()
        dates = [str(d) for d in dates]
        dstr = ",".join(f"'{d}'" for d in dates)

        # 1) 当日可交易（非 ST / 非停牌 / 有行情）
        flags = storage.query(f"""SELECT ts_code, trade_date, is_st, is_suspended
            FROM {src} WHERE trade_date IN ({dstr})""")
        if self.drop_st:
            flags = flags[flags["is_st"] == 0]
        if self.drop_suspended:
            flags = flags[flags["is_suspended"] == 0]
        flags = flags.copy()
        flags["trade_date"] = flags["trade_date"].astype(str)

        # 2) 上市满 min_list_days 个交易日
        tds = calendar.get_trade_dates("20050101", max(dates))
        pos = {d: i for i, d in enumerate(tds)}
        cut = {d: (tds[pos[d] - self.min_list_days] if pos.get(d, 0) >= self.min_list_days
                   else tds[0]) for d in dates}
        sb = storage.read_dim("stock_basic")[["ts_code", "list_date"]].copy()
        sb["list_date"] = sb["list_date"].astype(str)
        list_map = dict(zip(sb["ts_code"], sb["list_date"]))
        flags["cut"] = flags["trade_date"].map(cut)
        flags["list_date"] = flags["ts_code"].map(list_map)
        out = flags[flags["list_date"] <= flags["cut"]][KEYS]
        return out.reset_index(drop=True)


# --------------------------------------------------------------- 注册表
# 池工厂：接收 spec 中该池对应的取值（scalar / dict / None），返回 Pool 实例。
POOL_REGISTRY: dict[str, Callable[[object], Pool]] = {}
_COMBINATORS = {"and", "or", "then", "without"}


def register_pool(*names: str):
    def deco(fn: Callable[[object], Pool]):
        for nm in names:
            POOL_REGISTRY[nm] = fn
        return fn
    return deco


# --------------------------------------------------------------- spec 解析
def _parse_str(s: str) -> dict:
    """字符串简写 → 规范 dict spec（字符串是 dict 的纯语法糖）。

    支持：all / csi500 / hs300 / csi1000 / sz50 / index:CODE /
          size.bottom:K / size.top:K / size.q:LO-HI /
          liquidity.top:K / liquidity.bottom:K / liquidity.q:LO-HI /
          industry:CODE[,CODE...] / board:名称[,名称...] / custom:CODE[,CODE...]
    """
    s = s.strip()
    if s in ALIASES:
        return {"index": ALIASES[s]}
    if s == "all":
        return {"all": {}}
    if ":" in s:
        head, arg = s.split(":", 1)
        if head in ("index", "industry", "board", "custom"):
            return {head: arg}
        if "." in head:
            kind, sub = head.split(".", 1)      # size.bottom / liquidity.top ...
            if kind in ("size", "liquidity"):
                if sub in ("bottom", "top"):
                    return {kind: {sub: int(arg)}}
                if sub == "q":
                    lo, hi = arg.split("-")
                    return {kind: {"q": [float(lo), float(hi)]}}
    raise ValueError(f"无法解析的池简写：{s!r}")


def resolve_pool(spec) -> Pool:
    """把 spec（str | dict | Pool）解析成 Pool 对象。

    - Pool 实例：原样返回。
    - str：按简写解析（见 _parse_str）。
    - dict：单键。键为组合算子（and/or/then/without）→ 递归构建；否则查 POOL_REGISTRY。
    """
    if isinstance(spec, Pool) and not isinstance(spec, (str, dict)):
        return spec
    if isinstance(spec, str):
        spec = _parse_str(spec)
    if not isinstance(spec, dict) or len(spec) != 1:
        raise ValueError(f"池 spec 必须是单键 dict / 字符串 / Pool：{spec!r}")

    (key, val), = spec.items()
    if key in _COMBINATORS:
        from quant_factor.universe import compose  # 延迟导入避免循环
        subs = [resolve_pool(s) for s in val]
        if key == "and":
            return compose.And(subs)
        if key == "or":
            return compose.Or(subs)
        if key == "then":
            return compose.Then(subs)
        if key == "without":
            if len(subs) != 2:
                raise ValueError("without 需要恰好两个子池 [a, b]")
            return compose.Without(subs[0], subs[1])
    if key in POOL_REGISTRY:
        return POOL_REGISTRY[key](val)
    raise ValueError(f"未知的池类型：{key!r}（已注册：{sorted(POOL_REGISTRY)}）")


# --------------------------------------------------------------- 对外入口（向后兼容）
def universe_frame(dates: list[str], pool: str = config.UNIV_POOL,
                   min_list_days: int = config.UNIV_MIN_LIST_DAYS) -> pd.DataFrame:
    """多调仓日的可投域（长表：trade_date, ts_code）。

    先算基础池（干净可投集合），再套用 pool spec。签名与旧版一致：
    pool 默认 'csi500'，'all' 即全市场（仅基础池），亦可传 dict 组合。
    """
    dates = [str(d) for d in dates]
    base = BasePool(min_list_days=min_list_days).members(dates)
    if base.empty:
        return base
    return resolve_pool(pool).apply(base).reset_index(drop=True)


def get_universe(T: str, pool: str = config.UNIV_POOL,
                 min_list_days: int = config.UNIV_MIN_LIST_DAYS) -> set[str]:
    """单个调仓日的可投域集合。"""
    df = universe_frame([str(T)], pool, min_list_days)
    return set(df["ts_code"])
