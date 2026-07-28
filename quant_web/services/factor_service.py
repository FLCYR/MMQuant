"""因子服务：评估指标、IC 序列、分组收益、相关性矩阵。

因子评估较慢（需算 universe 与前向收益），结果按参数哈希缓存到
data/factor/eval/{key}.json，界面默认读缓存，显式触发才重算。
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

import config
from quant_factor import compute, evaluate
from quant_factor.factors import REGISTRY
from quant_factor.rebalance import get_rebalance_dates
from quant_web import jobs
from quant_web.serialize import dataclass_dict

EVAL_DIR = config.FACTOR_DIR / "eval"


def _key(names: list[str], start: str, end: str, freq: str, pool: str) -> str:
    raw = json.dumps({"n": sorted(names), "s": start, "e": end, "f": freq, "p": pool},
                     sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def list_factors() -> list[dict]:
    return [{"name": n, "style": f.style, "direction": f.direction}
            for n, f in REGISTRY.items()]


def get_cached(names, start, end, freq, pool) -> dict | None:
    f = EVAL_DIR / f"{_key(names, start, end, freq, pool)}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def evaluate_all(job_id: str, params: dict) -> dict:
    names = params.get("factors") or list(REGISTRY)
    start = params.get("start", "20160101")
    end = params.get("end") or None            # None → get_rebalance_dates 截到今天
    freq = params.get("freq", config.REBAL_FREQ)
    pool = params.get("pool", config.UNIV_POOL)

    jobs.progress(job_id, "准备调仓日", 5)
    dates = get_rebalance_dates(start, end, freq)

    jobs.progress(job_id, "读取因子面板", 15)
    panel = compute.read_panel("processed")
    if panel.empty:
        raise RuntimeError("因子面板为空，请先运行 scripts/build_factors.py")
    compute.check_dates_coverage(dates, panel)
    panel = panel[panel["trade_date"].astype(str).isin(set(dates))]

    reports = []
    for i, nm in enumerate(names):
        if nm not in panel.columns:
            continue
        jobs.progress(job_id, f"评估 {nm}", 20 + int(60 * i / max(len(names), 1)))
        r = evaluate.evaluate_factor(nm, panel, dates, pool=pool, freq=freq)
        d = dataclass_dict(r)
        d["verdict"] = r.verdict()
        reports.append(d)

    jobs.progress(job_id, "计算 IC 序列", 85)
    ic = evaluate.ic_frame(names, panel, dates, pool=pool)
    ic_payload = {}
    for nm in ic.columns:
        s = ic[nm].dropna()
        ic_payload[nm] = {
            "x": [str(i) for i in s.index],
            "ic": [float(v) for v in s.to_numpy()],
            "cum": [float(v) for v in s.cumsum().to_numpy()],   # 累计IC：最能看出因子是否持续有效
        }

    jobs.progress(job_id, "计算相关性", 95)
    corr = evaluate.factor_correlation(panel, [r["name"] for r in reports])
    corr_payload = {
        "names": list(corr.columns) if not corr.empty else [],
        "matrix": [[None if pd.isna(v) else float(v) for v in row]
                   for row in (corr.to_numpy() if not corr.empty else [])],
    }

    payload = {"params": {"factors": names, "start": start, "end": end,
                          "freq": freq, "pool": pool},
               "n_dates": len(dates),
               "reports": reports, "ic": ic_payload, "correlation": corr_payload}

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / f"{_key(names, start, end, freq, pool)}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload
