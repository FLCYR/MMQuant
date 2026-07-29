"""策略实盘跟踪（纸上模拟）状态落盘与检索。

目录（镜像 data/backtest/{run_id}/ 的既有约定）：
    data/live/{run_id}/
        state.json          策略/参数/现金/持仓/待执行信号/推进进度
        nav_history.parquet trade_date, nav, cash（逐日盯市，含非调仓日）
        trades.parquet      date, ts_code, side, amount, cost（字段与回测 trades 一致）

只做纸上模拟：不接触任何真实资金/账户，纯粹的状态记账 + 信号记录。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime

import pandas as pd

from quant_web import webconfig


def _dir(run_id: str):
    return webconfig.LIVE_DIR / run_id


def exists(run_id: str) -> bool:
    return _dir(run_id).exists()


def new_run_id(strategy_id: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{strategy_id}"


# ------------------------------------------------------------------ state.json
def save_state(run_id: str, state: dict) -> None:
    d = _dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state(run_id: str) -> dict | None:
    f = _dir(run_id) / "state.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def list_runs() -> list[dict]:
    """全部实例的 state（含 active/stopped），按创建时间倒序。"""
    if not webconfig.LIVE_DIR.exists():
        return []
    out = []
    for p in webconfig.LIVE_DIR.iterdir():
        f = p / "state.json"
        if f.exists():
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
    return sorted(out, key=lambda s: s.get("created_at", ""), reverse=True)


def active_run_ids() -> list[str]:
    return [s["run_id"] for s in list_runs() if s.get("status") == "active"]


def delete_run(run_id: str) -> bool:
    d = _dir(run_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


# ------------------------------------------------------------------ nav_history.parquet
def append_nav(run_id: str, trade_date: str, nav: float, cash: float) -> None:
    d = _dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "nav_history.parquet"
    row = pd.DataFrame([{"trade_date": str(trade_date), "nav": float(nav), "cash": float(cash)}])
    if path.exists():
        old = pd.read_parquet(path)
        old = old[old["trade_date"] != str(trade_date)]   # 幂等：同一天重复写入覆盖而非累加
        out = pd.concat([old, row], ignore_index=True)
    else:
        out = row
    out = out.sort_values("trade_date").reset_index(drop=True)
    out.to_parquet(path, index=False)


def load_nav(run_id: str) -> pd.DataFrame:
    f = _dir(run_id) / "nav_history.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["trade_date", "nav", "cash"])


# ------------------------------------------------------------------ trades.parquet
def append_trades(run_id: str, trades: list) -> None:
    """trades: execution.Trade 列表（date/ts_code/side/amount/cost）。"""
    if not trades:
        return
    d = _dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "trades.parquet"
    new = pd.DataFrame([t.__dict__ for t in trades])
    if path.exists():
        old = pd.read_parquet(path)
        out = pd.concat([old, new], ignore_index=True)
    else:
        out = new
    out.to_parquet(path, index=False)


def load_trades(run_id: str) -> pd.DataFrame:
    f = _dir(run_id) / "trades.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["date", "ts_code", "side", "amount", "cost"])
