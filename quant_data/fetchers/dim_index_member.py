"""历史成分股 dim_index_member（区间表）。

广基指数（如中证500）的历史成分由 index_weight 的月度权重快照反推：
按月拉快照 → 每个快照日得到成分集合 → 相邻快照差分出纳入/剔除，形成 in/out 区间。
粒度为月（半年度调整必然被覆盖），足够月度调仓的回测使用。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import config
from quant_data.client import TushareClient, get_client
from quant_data import storage
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.index_member")

FAR = None                        # 仍在成分内 → out_date 为空


def _month_ranges(start: str, end: str) -> list[tuple[str, str]]:
    rng = pd.period_range(start=pd.Timestamp(start), end=pd.Timestamp(end), freq="M")
    out = []
    for p in rng:
        out.append((p.start_time.strftime("%Y%m%d"), p.end_time.strftime("%Y%m%d")))
    return out


def _build_intervals(snapshots: dict[str, set[str]]) -> list[tuple[str, str, str | None]]:
    """由 {快照日: 成分集合} 差分出 (ts_code, in_date, out_date) 区间。"""
    dates = sorted(snapshots)
    rows: list[tuple[str, str, str | None]] = []
    open_in: dict[str, str] = {}
    prev: set[str] = set()
    for d in dates:
        cur = snapshots[d]
        for c in cur - prev:            # 新纳入
            open_in[c] = d
        for c in prev - cur:            # 被剔除：out_date = 首个不在成分的快照日
            rows.append((c, open_in.pop(c, dates[0]), d))
        prev = cur
    for c, ind in open_in.items():      # 仍在成分内
        rows.append((c, ind, FAR))
    return rows


def sync(client: TushareClient | None = None, index_code: str = config.CSI500,
         start: str = config.START_DATE, end: str | None = None) -> int:
    client = client or get_client()
    end = end or datetime.now().strftime("%Y%m%d")

    snapshots: dict[str, set[str]] = {}
    for ms, me in _month_ranges(start, end):
        w = client.call("index_weight", index_code=index_code,
                        start_date=ms, end_date=me,
                        fields="index_code,con_code,trade_date,weight")
        if w.empty:
            continue
        for d, g in w.groupby("trade_date"):
            snapshots[str(d)] = set(g["con_code"])

    if not snapshots:
        log.warning("%s 未取到任何成分快照", index_code)
        return 0

    rows = _build_intervals(snapshots)
    df = pd.DataFrame(rows, columns=["ts_code", "in_date", "out_date"])
    df.insert(0, "index_code", index_code)
    df["in_date"] = df["in_date"].astype("string")
    df["out_date"] = df["out_date"].astype("string")
    df = df.sort_values(["ts_code", "in_date"]).reset_index(drop=True)

    # 保留其他指数的既有记录，只替换本指数
    old = storage.read_dim("index_member")
    if not old.empty:
        old = old[old["index_code"] != index_code]
        df = pd.concat([old, df], ignore_index=True)
    n = storage.write_dim("index_member", df)

    n_snap = len(snapshots)
    last = sorted(snapshots)[-1]
    log.info("%s 成分区间同步完成：%d 个快照，%d 条区间，最新快照 %s 成分 %d 只",
             index_code, n_snap, len(rows), last, len(snapshots[last]))
    return len(rows)


if __name__ == "__main__":
    sync()
