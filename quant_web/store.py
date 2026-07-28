"""回测结果落盘与检索。

目录（新增，不影响既有 data/ 结构）：
    data/backtest/{run_id}/
        meta.json        参数 + 绩效指标 + 创建时间
        nav.parquet      trade_date, nav, cash, nav_zero, bench
        trades.parquet   date, ts_code, side, amount, cost
        positions.parquet trade_date, ts_code, weight
        turnover.parquet trade_date, turnover
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime

import pandas as pd

from quant_web import webconfig


def _dir(run_id: str):
    return webconfig.RUNS_DIR / run_id


def new_run_id(params: dict) -> str:
    """run_id 里塞策略 id + 几个常见参数（若存在），仅供人眼辨识，不同策略字段不同故用 .get 兜底。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sp = params.get("strategy_params") or {}
    bits = [params.get("strategy") or "strategy"]
    if sp.get("combiner") == "ic":
        bits.append("ic")
    if sp.get("topn") is not None:
        bits.append(f"top{sp['topn']}")
    if sp.get("industry_neutral"):
        bits.append("ind")
    return f"{ts}_{'_'.join(bits)}"


def save_run(run_id: str, params: dict, nav: pd.DataFrame, trades: pd.DataFrame,
             positions: pd.DataFrame, turnover: pd.Series, perfs: list[dict]) -> dict:
    d = _dir(run_id)
    d.mkdir(parents=True, exist_ok=True)

    nav.to_parquet(d / "nav.parquet", index=True)
    (trades if trades is not None else pd.DataFrame()).to_parquet(d / "trades.parquet", index=False)
    (positions if positions is not None else pd.DataFrame()).to_parquet(d / "positions.parquet", index=False)
    turnover.rename("turnover").to_frame().to_parquet(d / "turnover.parquet", index=True)

    meta = {
        "run_id": run_id,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": params,
        "performance": perfs,
        "n_trades": int(len(trades)) if trades is not None else 0,
        "start": str(nav.index[0]) if len(nav) else None,
        "end": str(nav.index[-1]) if len(nav) else None,
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def list_runs() -> list[dict]:
    if not webconfig.RUNS_DIR.exists():
        return []
    out = []
    for p in webconfig.RUNS_DIR.iterdir():
        f = p / "meta.json"
        if f.exists():
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
    return sorted(out, key=lambda m: m.get("created_at", ""), reverse=True)


def load_meta(run_id: str) -> dict | None:
    f = _dir(run_id) / "meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _load(run_id: str, name: str) -> pd.DataFrame:
    f = _dir(run_id) / f"{name}.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame()


def load_nav(run_id: str) -> pd.DataFrame:
    return _load(run_id, "nav")


def load_trades(run_id: str) -> pd.DataFrame:
    return _load(run_id, "trades")


def load_positions(run_id: str) -> pd.DataFrame:
    return _load(run_id, "positions")


def load_turnover(run_id: str) -> pd.DataFrame:
    return _load(run_id, "turnover")


def delete_run(run_id: str) -> bool:
    d = _dir(run_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False
