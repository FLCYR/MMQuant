"""调仓 / 因子评估日。一切以交易日历为准。

周频：每 ISO 周取最后一个交易日；月频：每月最后一个交易日；日频：全部交易日。
"""
from __future__ import annotations

import datetime as _dt

import config
from quant_data import calendar


def _today() -> str:
    return _dt.date.today().strftime("%Y%m%d")


def _key(d: str, freq: str):
    dt = _dt.date(int(d[:4]), int(d[4:6]), int(d[6:8]))
    if freq == "W":
        iso = dt.isocalendar()
        return (iso[0], iso[1])          # (ISO 年, ISO 周)
    if freq == "M":
        return d[:6]
    raise ValueError(f"未知频率：{freq}")


def get_rebalance_dates(start: str = config.START_DATE, end: str | None = None,
                        freq: str = config.REBAL_FREQ) -> list[str]:
    dates = calendar.get_trade_dates(start, end or _today())  # 默认截到今天，排除未来交易日
    if freq == "D":
        return dates
    last: dict = {}
    for d in dates:
        last[_key(d, freq)] = d          # 覆盖 → 每组最后一个交易日
    return sorted(last.values())
