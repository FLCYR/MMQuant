"""池组合算子：交集 / 并集 / 串联 / 排除。

语义（均在同一上游 frame 之上）：
- And   ：各子池独立筛上游后取**交集**（如 中证500 ∩ 高流动性）。
- Or    ：各子池筛上游后取**并集**（如 沪深300 ∪ 中证500）。
- Then  ：**串联/管道**——后一池在前一池的输出内继续筛。排序型池（SizePool）
          在成员池之后必须用 Then，例如"中证500 → 市值最小 200 只"。
- Without：a 减去 b（如 全市场 排除某行业）。

frame 语义天然支持：交集=merge、并集=concat+dedup、排除=anti-join。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant_factor.universe.base import KEYS, Pool


@dataclass
class And:
    pools: list[Pool]

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        if not self.pools:
            return up
        out = self.pools[0].apply(up)[KEYS]
        for p in self.pools[1:]:
            out = out.merge(p.apply(up)[KEYS], on=KEYS)
        return out.reset_index(drop=True)


@dataclass
class Or:
    pools: list[Pool]

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        if not self.pools:
            return up.iloc[0:0]
        frames = [p.apply(up)[KEYS] for p in self.pools]
        return pd.concat(frames, ignore_index=True).drop_duplicates(KEYS).reset_index(drop=True)


@dataclass
class Then:
    """管道：依次把上游喂给每个池，后者在前者输出内筛选。"""

    pools: list[Pool]

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        cur = up
        for p in self.pools:
            cur = p.apply(cur)
        return cur.reset_index(drop=True)


@dataclass
class Without:
    a: Pool
    b: Pool

    def apply(self, up: pd.DataFrame) -> pd.DataFrame:
        fa = self.a.apply(up)[KEYS]
        fb = self.b.apply(up)[KEYS]
        ind = fa.merge(fb, on=KEYS, how="left", indicator=True)
        return ind[ind["_merge"] == "left_only"][KEYS].reset_index(drop=True)
