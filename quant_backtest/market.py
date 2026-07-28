"""行情容器 MarketData：回测引擎唯一的行情来源（依赖注入）。

生产用 `MarketData.from_storage(...)` 读真实数据；测试直接构造极小的合成行情表，
从而能对涨停/跌停/停牌/退市等分支做确定性断言——这是回测可靠性的关键设计。

约定（全部为宽表：index=trade_date 升序，columns=ts_code）：
- adj_open / adj_close：后复权价（open|close × adj_factor）
- buyable / sellable  ：布尔，可否在当日开盘买入/卖出（用未复权原始价与涨跌停同口径判定）
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from quant_data import storage

_DATE_RE = re.compile(r"^\d{8}$")


def _validate_date(d: str) -> str:
    """校验 YYYYMMDD 格式——start/end 常来自 HTTP 请求体，拼入/传入 SQL 前必须校验。"""
    d = str(d)
    if not _DATE_RE.match(d):
        raise ValueError(f"日期格式非法（应为 YYYYMMDD）：{d!r}")
    return d


@dataclass
class MarketData:
    adj_open: pd.DataFrame
    adj_close: pd.DataFrame
    buyable: pd.DataFrame
    sellable: pd.DataFrame

    def __post_init__(self):
        self.dates: list[str] = [str(d) for d in self.adj_close.index]
        # 停牌日 adj_close 为空，盯市需用最后成交价（前值填充），否则净值会漏算持仓
        self.mark_close: pd.DataFrame = self.adj_close.ffill()
        # 每只股票最后一个有价日，用于退市强平
        self.last_quote: dict[str, str] = {}
        for c in self.adj_close.columns:
            s = self.adj_close[c].dropna()
            if len(s):
                self.last_quote[c] = str(s.index[-1])

    # ------------------------------------------------------------------
    @classmethod
    def from_storage(cls, start: str, end: str,
                     ts_codes: list[str] | None = None) -> "MarketData":
        start, end = _validate_date(start), _validate_date(end)
        src = storage.fact_source("daily_quote")
        if not src:
            raise RuntimeError("daily_quote 无数据，请先 backfill")
        cond = "trade_date >= ? AND trade_date <= ?"
        params: list = [start, end]
        if ts_codes:
            codes = sorted(set(ts_codes))
            cond += f" AND ts_code IN ({','.join(['?'] * len(codes))})"
            params += codes
        df = storage.query(f"""
            SELECT ts_code, CAST(trade_date AS VARCHAR) AS trade_date,
                   open*adj_factor  AS adj_open,
                   close*adj_factor AS adj_close,
                   open, limit_up, limit_down, is_suspended
            FROM {src} WHERE {cond}
        """, params)
        if df.empty:
            raise RuntimeError("行情为空，检查区间/股票范围")

        # 可交易性：停牌不可交易；开盘涨停买不进、开盘跌停卖不掉（涨跌停价缺失则视为可交易）
        ok = (df["is_suspended"] == 0) & df["open"].notna()
        df["buyable"] = ok & (df["limit_up"].isna() | (df["open"] < df["limit_up"]))
        df["sellable"] = ok & (df["limit_down"].isna() | (df["open"] > df["limit_down"]))

        piv = lambda col: df.pivot(index="trade_date", columns="ts_code", values=col).sort_index()
        return cls(
            adj_open=piv("adj_open"),
            adj_close=piv("adj_close"),
            buyable=piv("buyable").fillna(False).astype(bool),
            sellable=piv("sellable").fillna(False).astype(bool),
        )

    # ------------------------------------------------------------------
    def open_prices(self, date: str) -> pd.Series:
        return self.adj_open.loc[date]

    def close_prices(self, date: str) -> pd.Series:
        return self.adj_close.loc[date]

    def mark_prices(self, date: str) -> pd.Series:
        """盯市用价格：停牌日沿用最后成交价。"""
        return self.mark_close.loc[date]

    def is_buyable(self, date: str, code: str) -> bool:
        try:
            return bool(self.buyable.at[date, code])
        except KeyError:
            return False

    def is_sellable(self, date: str, code: str) -> bool:
        try:
            return bool(self.sellable.at[date, code])
        except KeyError:
            return False

    def delisted_by(self, date: str, code: str) -> bool:
        """该股在 date 之前已无后续行情（退市/长期停更）。"""
        lq = self.last_quote.get(code)
        return lq is not None and date > lq
