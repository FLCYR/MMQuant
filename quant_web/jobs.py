"""异步任务：回测耗时数分钟，HTTP 不能同步等待。

单进程线程池（默认并发 1，回测吃内存）。前端 POST 拿 job_id 后轮询状态。
"""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from quant_web import webconfig

_pool = ThreadPoolExecutor(max_workers=webconfig.MAX_CONCURRENT_JOBS)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _set(job_id: str, **kw):
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def progress(job_id: str, message: str, pct: int | None = None):
    """供任务函数回报进度。"""
    kw = {"message": message}
    if pct is not None:
        kw["progress"] = int(pct)
    _set(job_id, **kw)


def submit(fn, *args, **kwargs) -> str:
    """提交任务；fn 的第一个参数会收到 job_id，用于回报进度。"""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"job_id": job_id, "status": "PENDING", "progress": 0,
                         "message": "排队中", "result": None, "error": None,
                         "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    def _run():
        _set(job_id, status="RUNNING", message="开始", progress=1)
        try:
            res = fn(job_id, *args, **kwargs)
            _set(job_id, status="SUCCESS", progress=100, message="完成", result=res)
        except Exception as e:
            _set(job_id, status="FAILED", message=str(e),
                 error=traceback.format_exc()[-2000:])

    _pool.submit(_run)
    return job_id


def get(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def list_jobs() -> list[dict]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
