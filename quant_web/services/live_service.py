"""策略实盘跟踪（纸上模拟）：把回测引擎"拆成按天推进"，状态跨天持久化。

核心设计：T 日收盘算出的目标权重，要到 T+1 开盘才执行——这个"信号→执行"的跨天关系，
在回测里是同一次调用内用 dates[i+1] 查表完成的；这里天然对应"今天算的信号，留到明天
这次调用里、用明天已经到手的开盘价去执行"。因此可以直接复用回测层的撮合/账本/成本模型
与策略注册表，策略代码本身不知道自己是在回测还是在实盘里跑。

**只做纸上模拟**：不接触任何真实资金/账户，纯粹的状态记账与信号记录。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

import config
from quant_backtest.costs import CostModel
from quant_backtest.execution import liquidate, rebalance
from quant_backtest.market import MarketData
from quant_backtest.portfolio import Portfolio
from quant_data import calendar, storage
from quant_factor import compute
from quant_factor.rebalance import get_rebalance_dates
from quant_strategy import strategies as _strategies  # noqa: F401  触发所有 @register_strategy
from quant_strategy import base as strat_base
from quant_web import jobs, live_store

DEFAULT_STRATEGY = "multifactor"
WINDOW_DAYS = 30          # 单日推进取的行情窗口（自然日），覆盖长假足够
SIGNAL_LOOKBACK = 120     # 构建策略时回看的调仓日数量上限（周频约2.3年，覆盖 RollingIC
                          # 所需的 26 期历史绰绰有余）；不能传全历史——否则每次调仓日都要
                          # 重算全历史 universe/panel 分组，随运行时长增长而越跑越慢。


# ------------------------------------------------------------------ 只读辅助
def _latest_quote_date() -> str | None:
    """真实可用的最新交易日——不只看 trade_date 是否存在，还要求当天确有部分股票的
    收盘价非空。上游数据源有时会出现"当天已有行、但价格字段整体缺失"的情况（如
    adj_factor 已发布、daily 行情接口还没跟上），此时该日尚不可用，回退到上一个
    确有数据的交易日，否则新建的实例会因当天数据是空壳而拿到一个空的初始信号。"""
    src = storage.fact_source("daily_quote")
    if not src:
        return None
    r = storage.query(f"""
        SELECT trade_date FROM {src}
        GROUP BY trade_date HAVING count(close) > 0
        ORDER BY trade_date DESC LIMIT 1
    """)
    return str(r.iloc[0]["trade_date"]) if len(r) else None


def _is_rebalance_day(d: str, freq: str) -> bool:
    """d 是否为其所在周期（周/月）的最后一个交易日。

    注意：不能用 get_rebalance_dates(start, end=d, freq) 的旧写法——把日历截断在 d 当天，
    d 之后同周期的交易日根本没被纳入比较，会让 d 永远"看起来"是自己周期的最后一天，
    导致每天都被误判为调仓日（此前的实际 bug，曾造成面板被逐日重写、跑得极慢）。
    正确做法：往前看一小段缓冲窗口，确认 d 之后没有同周期的交易日。"""
    d = str(d)
    buffer_days = 10 if freq == "W" else 35      # 一周/一月的自然日上限，留足冗余
    end = (datetime.strptime(d, "%Y%m%d") + timedelta(days=buffer_days)).strftime("%Y%m%d")
    grouped = get_rebalance_dates(start=d, end=end, freq=freq)
    return bool(grouped) and grouped[0] == d


def _bounded_rebal_dates(d: str, freq: str) -> list[str]:
    """构建策略用的调仓日历史，截取最近 SIGNAL_LOOKBACK 期（含 d 本身），避免每次都
    重算全历史 universe/panel 分组。"""
    dates = get_rebalance_dates(config.START_DATE, d, freq)
    if not dates or dates[-1] != str(d):
        dates = dates + [str(d)]
    return dates[-SIGNAL_LOOKBACK:]


_FRESHNESS_COLS = ("REV20", "VOL20", "TURN20")   # 价量因子，用于判断某日是否真有数据


def _ensure_panel_covers(d: str, freq: str) -> pd.DataFrame:
    """面板若还没覆盖到 d、或该日行情当时还没到齐导致整行价量因子全空（"僵尸日期"——
    历史上曾在数据未拉全时被计算过一次，增量 upsert 设计不会自动重算已存在的日期，
    空值会一直沉在面板里），增量重算这一天（复用 compute.build 的反连接式 upsert）。"""
    d = str(d)
    panel = compute.read_panel("processed")
    existing = panel[panel["trade_date"].astype(str) == d] if not panel.empty else panel
    cols = [c for c in _FRESHNESS_COLS if c in panel.columns]
    stale = existing.empty or (cols and existing[cols].notna().to_numpy().sum() == 0)
    if stale:
        compute.build(start=d, end=d, freq=freq)
        panel = compute.read_panel("processed")
    return panel


def _market_window(d: str, codes: set) -> MarketData:
    end_dt = datetime.strptime(str(d), "%Y%m%d")
    start = (end_dt - timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d")
    return MarketData.from_storage(start, str(d), sorted(codes))


def _positions_detail(state: dict) -> list[dict]:
    units = state.get("units") or {}
    if not units:
        return []
    codes = sorted(units)
    src = storage.fact_source("daily_quote")
    placeholders = ",".join(["?"] * len(codes))
    latest = storage.query(f"""
        WITH ranked AS (
            SELECT ts_code, close, adj_factor,
                   row_number() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
            FROM {src} WHERE ts_code IN ({placeholders})
        )
        SELECT ts_code, close, adj_factor FROM ranked WHERE rn = 1
    """, codes)
    price_map = {r.ts_code: float(r.close) * float(r.adj_factor) for r in latest.itertuples()}
    sb = storage.read_dim("stock_basic")
    name_map = dict(zip(sb["ts_code"], sb["name"])) if not sb.empty else {}

    vals = {c: units[c] * price_map.get(c, 0.0) for c in codes}
    total = sum(vals.values())
    rows = [{
        "ts_code": c, "name": name_map.get(c, ""), "units": units[c],
        "price": price_map.get(c), "value": vals[c],
        "weight": (vals[c] / total) if total > 0 else None,
    } for c in codes]
    return sorted(rows, key=lambda r: r["value"] or 0, reverse=True)


# ------------------------------------------------------------------ 单日推进（核心）
def _advance_one_day(state: dict, d: str) -> list:
    """把 state 原地推进一天：强平已无行情标的 → 执行昨日信号 → 盯市 → 若为调仓日则算新信号
    （留到下次推进执行，杜绝当天算当天买的未来函数）。返回本日成交 Trade 列表。"""
    pending = state.get("pending_signal")
    units = state["units"]
    codes = set(units) | set((pending or {}).get("weights", {}))

    pf = Portfolio(state["cash"])
    pf.units = dict(units)
    trades_today: list = []

    if codes:
        mkt = _market_window(d, codes)
        # 数据源当天是否真的出全了行情：回测里"某股票窗口内查无行情"=真退市（历史数据是终态、
        # 完整的），但实盘每天现拉的数据可能当天还没出全（如 Tushare 未及时更新）——此时如果不
        # 分辨，delisted_by 会把当天全市场误判成"全部退市"，把整个组合强制清空。用"当天是否
        # 有任何一只股票有有效收盘价"来判断数据是否可信；不可信则本日既不强平也不执行信号，
        # 原样留到下次推进重试。
        day_has_data = d in mkt.adj_close.index and bool(mkt.adj_close.loc[d].notna().any())
        if day_has_data:
            for code in list(pf.units):                   # 强制平仓：窗口内已无行情（真退市）
                if mkt.delisted_by(d, code):
                    lq = mkt.last_quote.get(code)
                    price = mkt.adj_close.at[lq, code] if lq else None
                    t = liquidate(pf, code, price, d, CostModel())
                    if t:
                        trades_today.append(t)
            if pending and pending["signal_date"] < d:     # 执行昨天算出的信号
                w = pd.Series(pending["weights"], dtype=float)
                trades_today.extend(rebalance(pf, w, d, mkt, CostModel()))
                pending = None
        nav = pf.value(mkt.mark_prices(d))
    else:
        nav = pf.cash

    state["cash"], state["units"], state["pending_signal"] = pf.cash, dict(pf.units), pending

    if _is_rebalance_day(d, state["freq"]):
        panel = _ensure_panel_covers(d, state["freq"])
        rebal_dates = _bounded_rebal_dates(d, state["freq"])
        ctx = strat_base.StrategyContext(dates=rebal_dates, pool=state["pool"],
                                         get_panel=lambda: panel, progress=lambda m, p: None)
        strat = strat_base.build(state["strategy"], state["strategy_params"], ctx)
        w = strat.target_weights(d)
        state["pending_signal"] = ({"signal_date": d, "weights": {k: float(v) for k, v in w.items()}}
                                   if len(w) else None)

    state["last_advanced_date"] = d
    live_store.append_nav(state["run_id"], d, nav, state["cash"])
    return trades_today


# ------------------------------------------------------------------ 对外入口
def create(job_id: str, params: dict) -> dict:
    p = params or {}
    strategy_id = p.get("strategy") or DEFAULT_STRATEGY
    strategy_params = {**strat_base.default_params(strategy_id), **(p.get("strategy_params") or {})}
    pool = p.get("pool") or config.UNIV_POOL
    freq = p.get("freq") or config.REBAL_FREQ
    cash = float(p.get("cash") or config.INIT_CASH)

    jobs.progress(job_id, "确定起始交易日", 10)
    today = _latest_quote_date()
    if today is None:
        raise RuntimeError("daily_quote 无数据，请先 backfill")

    run_id = live_store.new_run_id(strategy_id)
    state = {
        "run_id": run_id,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": strategy_id, "strategy_params": strategy_params,
        "pool": pool, "freq": freq, "init_cash": cash,
        "status": "active",
        "last_advanced_date": today,
        "pending_signal": None,
        "cash": cash, "units": {},
    }

    jobs.progress(job_id, "计算初始信号", 40)
    panel = _ensure_panel_covers(today, freq)
    # 创建日视为"强制调仓日"：_bounded_rebal_dates 会把 today 强行并入调仓日历，
    # 不然要等到下个自然周期才会建仓；同时截取最近 SIGNAL_LOOKBACK 期，避免全历史重算
    rebal_dates = _bounded_rebal_dates(today, freq)
    ctx = strat_base.StrategyContext(dates=rebal_dates, pool=pool, get_panel=lambda: panel,
                                     progress=lambda m, pct: jobs.progress(job_id, m, pct))
    strat = strat_base.build(strategy_id, strategy_params, ctx)
    w = strat.target_weights(today)
    state["pending_signal"] = ({"signal_date": today, "weights": {k: float(v) for k, v in w.items()}}
                               if len(w) else None)

    jobs.progress(job_id, "落盘", 90)
    live_store.append_nav(run_id, today, cash, cash)     # 首日空仓，nav=初始资金
    live_store.save_state(run_id, state)
    return state


def advance(run_id: str) -> dict:
    """把单个实例推进到最新可用交易日（若漏跑多天，逐日补齐）。"""
    state = live_store.load_state(run_id)
    if not state:
        raise ValueError(f"未知实例：{run_id}")
    if state.get("status") != "active":
        return {"advanced": [], "message": "实例已停止，未推进"}

    today = _latest_quote_date()
    if today is None:
        raise RuntimeError("daily_quote 无数据")
    all_trading = calendar.get_trade_dates(state["last_advanced_date"], today)
    todo = [d for d in all_trading if d > state["last_advanced_date"]]
    if not todo:
        return {"advanced": [], "message": "已是最新，无需推进"}

    n_trades = 0
    for d in todo:
        trades = _advance_one_day(state, d)
        live_store.append_trades(run_id, trades)
        live_store.save_state(run_id, state)      # 逐日落盘，防中途失败丢进度
        n_trades += len(trades)
    return {"advanced": todo, "n_trades": n_trades}


def advance_all() -> dict:
    """遍历全部 active 实例逐一推进；单个实例失败不影响其他实例（供 scripts/run_live.py 调用）。"""
    results = {}
    for run_id in live_store.active_run_ids():
        try:
            results[run_id] = advance(run_id)
        except Exception as e:
            results[run_id] = {"error": str(e)}
    return results


def list_live() -> list[dict]:
    out = []
    for state in live_store.list_runs():
        nav = live_store.load_nav(state["run_id"])
        latest_nav = float(nav["nav"].iloc[-1]) if len(nav) else state["init_cash"]
        out.append({
            **state,
            "latest_nav": latest_nav,
            "total_return": latest_nav / state["init_cash"] - 1 if state["init_cash"] else None,
        })
    return out


def detail(run_id: str) -> dict | None:
    state = live_store.load_state(run_id)
    if not state:
        return None
    nav = live_store.load_nav(run_id)
    trades = live_store.load_trades(run_id)
    return {
        "state": state,
        "nav_history": {
            "x": nav["trade_date"].astype(str).tolist() if len(nav) else [],
            "nav": [float(v) for v in nav["nav"]] if len(nav) else [],
            "cash": [float(v) for v in nav["cash"]] if len(nav) else [],
        },
        "positions": _positions_detail(state),
        "pending_signal": state.get("pending_signal"),
        "n_trades": int(len(trades)),
        "recent_trades": (trades.sort_values("date", ascending=False).head(100).to_dict("records")
                          if len(trades) else []),
    }


def stop(run_id: str) -> bool:
    state = live_store.load_state(run_id)
    if not state:
        return False
    state["status"] = "stopped"
    live_store.save_state(run_id, state)
    return True


def delete(run_id: str) -> bool:
    return live_store.delete_run(run_id)
