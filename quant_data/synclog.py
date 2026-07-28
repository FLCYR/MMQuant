"""同步日志 meta_sync_log：增量更新与失败回补的依据。

每次任务启动时查出非 SUCCESS 的日期优先补数（幂等可重跑）。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant_data import storage

SYNC_KEYS = ["task_name", "biz_date"]


def record(task_name: str, biz_date: str, status: str, row_count: int = 0,
           retry_times: int = 0, error_msg: str = "",
           start_time: datetime | None = None, end_time: datetime | None = None) -> None:
    now = datetime.now()
    df = pd.DataFrame([{
        "task_name": task_name,
        "biz_date": str(biz_date),
        "status": status,                 # SUCCESS / FAILED / PARTIAL
        "row_count": int(row_count),
        "retry_times": int(retry_times),
        "error_msg": (error_msg or "")[:500],
        "start_time": start_time or now,
        "end_time": end_time or now,
    }])
    storage.write_meta("sync_log", df, SYNC_KEYS)


def get_status(task_name: str) -> dict[str, str]:
    df = storage.read_meta("sync_log")
    if df.empty:
        return {}
    sub = df[df["task_name"] == task_name]
    return dict(zip(sub["biz_date"].astype(str), sub["status"]))


def done_dates(task_name: str, status: str = "SUCCESS") -> set[str]:
    return {d for d, s in get_status(task_name).items() if s == status}


def pending_dates(task_name: str, all_dates: list[str]) -> list[str]:
    """all_dates 中尚未成功（失败或从未处理）的日期。"""
    done = done_dates(task_name)
    return [d for d in all_dates if d not in done]
