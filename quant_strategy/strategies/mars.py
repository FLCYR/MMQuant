"""MARS 策略：大盘放量（流动性溢价） + 个股涨停回踩（技术结构）双重共振的打板策略。

与多因子那种"周期截面再平衡"完全不同，MARS 是**事件驱动 + 买入持有到止损**：任意
交易日都可能冒出涨停信号，确认后建仓、之后按每只票各自的止损/止盈独立管理，直到出场。
因此它不套用"目标权重再平衡"引擎（那样会被逐日拉回等权、凭空空转），而是自带一个小型
回测驱动 `mars_driver`，复用回测层的 Portfolio/CostModel/execution/MarketData/metrics，
只是把每日循环换成"买入持有"语义。策略逻辑全部关在本模块内，不改动引擎与其它策略。

信号（以涨停发生日 T 为基准，全部用交易日历口径）：
- 条件A 大盘放量：沪市成交额(000001.SH amount÷1e5, 亿元) V(T)-V(T-1) > 阈值(默认600亿)
- 条件B 强势涨停：T 日封涨停(close==limit_up)，且满足 反包/突破60日新高/均线多头 之一
- 条件C 回踩抗跌：T+1、T+2 两日最低价都 ≥ 涨停中位线 P_mid=(pre_close_T+limit_up_T)/2
- 条件D 缩量：max(vol_{T+1}, vol_{T+2}) < 缩量比例(默认0.8) × vol_T
- 满足则 T+2 确认 → **T+3 开盘建仓**（引擎口径：信号日收盘信息 → 次日开盘成交，无前视）
- 出场：收盘跌破 P_mid（硬止损）或从买入后最高收盘回撤>移动止盈比例(默认5%) → 次日开盘清仓

口径说明：涨停/中位线/止损用**原始价**（涨停是原始价概念）；均线/60日新高用**后复权价**
（跨除权连续）；成交/盯市沿用引擎的后复权开盘价。持有期通常很短，除权影响忽略。
中位线 P_mid=(pre_close+limit_up)/2 天然兼容主板10%/创业板科创板20%/ST 5%，无需硬编码涨幅。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from quant_data import calendar, storage
from quant_backtest.costs import CostModel
from quant_backtest.engine import BacktestResult
from quant_backtest.execution import liquidate, rebalance
from quant_backtest.market import MarketData
from quant_backtest.portfolio import Portfolio
from quant_factor import universe
from quant_strategy.base import StrategyContext, register_strategy

WARMUP_DAYS = 90            # 建仓区间前多取的自然日，供 MA60/60日新高预热
SH_INDEX = "000001.SH"     # 上证综指，其 amount 即沪市全天成交额


# ------------------------------------------------------------------ 数据读取
def _scan_codes(pool, ref_date: str) -> list[str]:
    """扫描universe：用某一参考日解析一次 pool（板块池与日期无关，指数池取该日成分）。"""
    uf = universe.universe_frame([ref_date], pool=pool)
    return sorted(uf["ts_code"].unique()) if not uf.empty else []


def _load_bars(codes: list[str], start: str, end: str) -> pd.DataFrame:
    """读扫描域的原始日线（含复权因子），long 格式。只取需要的列，省内存。"""
    src = storage.fact_source("daily_quote")
    if not src or not codes:
        return pd.DataFrame()
    ph = ",".join(["?"] * len(codes))
    df = storage.query(f"""
        SELECT ts_code, CAST(trade_date AS VARCHAR) AS trade_date,
               open, high, low, close, pre_close, vol, adj_factor,
               limit_up, is_suspended
        FROM {src}
        WHERE trade_date >= ? AND trade_date <= ? AND ts_code IN ({ph})
        ORDER BY ts_code, trade_date
    """, [start, end, *codes])
    return df


def _sh_turnover(start: str, end: str) -> pd.Series:
    """沪市成交额（亿元），index=trade_date。"""
    src = storage.fact_source("index_daily")
    if not src:
        return pd.Series(dtype=float)
    df = storage.query(f"""
        SELECT CAST(trade_date AS VARCHAR) AS trade_date, amount
        FROM {src} WHERE index_code = ? AND trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
    """, [SH_INDEX, start, end])
    if df.empty:
        return pd.Series(dtype=float)
    return (df.set_index("trade_date")["amount"] / 1e5)          # 千元 → 亿元


# ------------------------------------------------------------------ 信号扫描
@dataclass
class _Signal:
    code: str
    entry_date: str        # 建仓执行日（T+3 开盘）
    exit_date: str | None  # 计划清仓执行日；None=持有到区间末


def _scan_one(g: pd.DataFrame, surge_ok: set, all_dates: list[str], pos: dict,
              params: dict) -> list[_Signal]:
    """单只股票扫描出全部 MARS 信号。g 已按 trade_date 升序，含预热段。"""
    g = g.reset_index(drop=True)
    n = len(g)
    if n < 61:
        return []
    tol = 1e-4
    shrink = float(params["pullback_shrink"])
    trail = float(params["trail_stop"])
    end_date = params["_end"]

    close = g["close"].to_numpy(dtype=float)
    high = g["high"].to_numpy(dtype=float)
    low = g["low"].to_numpy(dtype=float)
    openp = g["open"].to_numpy(dtype=float)
    pre_close = g["pre_close"].to_numpy(dtype=float)
    vol = g["vol"].to_numpy(dtype=float)
    limit_up = g["limit_up"].to_numpy(dtype=float)
    susp = g["is_suspended"].to_numpy(dtype=float)
    adjf = g["adj_factor"].to_numpy(dtype=float)
    dates = g["trade_date"].tolist()

    ac = close * adjf                                 # 后复权收盘
    ah = high * adjf
    ao = openp * adjf
    s = pd.Series(ac)
    ma5, ma10 = s.rolling(5).mean().to_numpy(), s.rolling(10).mean().to_numpy()
    ma20, ma60 = s.rolling(20).mean().to_numpy(), s.rolling(60).mean().to_numpy()
    prior_high60 = pd.Series(ah).rolling(60).max().shift(1).to_numpy()   # T 之前 60 日最高

    out: list[_Signal] = []
    for t in range(60, n - 2):                        # 需要 T-60 预热 与 T+1,T+2
        d = dates[t]
        if limit_up[t] <= 0 or susp[t] == 1:
            continue
        if close[t] < limit_up[t] - tol:              # B: 封死涨停
            continue
        if d not in surge_ok:                         # A: 大盘放量
            continue
        # B: 反包 / 突破 / 主升 三选一（后复权）
        fanbao = ac[t] > ah[t - 1] and close[t - 1] < openp[t - 1]
        breakout = prior_high60[t] == prior_high60[t] and ac[t] > prior_high60[t]
        bull = ma5[t] > ma10[t] > ma20[t] > ma60[t]
        if not (fanbao or breakout or bull):
            continue
        # C+D: 回踩两日不破中位 + 缩量（原始价/量）
        if susp[t + 1] == 1 or susp[t + 2] == 1 or vol[t] <= 0:
            continue
        p_mid = (pre_close[t] + limit_up[t]) / 2.0
        if min(low[t + 1], low[t + 2]) < p_mid:
            continue
        if max(vol[t + 1], vol[t + 2]) >= shrink * vol[t]:
            continue
        # 建仓执行日 = T+2 之后第一个交易日（用全市场交易日历，跨过本股停牌也算）
        entry = _next_trade_date(dates[t + 2], all_dates, pos)
        if entry is None or entry < params["_start"] or entry > end_date:
            continue
        exit_d = _plan_exit(g, t + 2, p_mid, trail, all_dates, pos, end_date)
        out.append(_Signal(g["ts_code"].iloc[0], entry, exit_d))
    return out


def _next_trade_date(d: str, all_dates: list[str], pos: dict) -> str | None:
    i = pos.get(d)
    return all_dates[i + 1] if i is not None and i + 1 < len(all_dates) else None


def _plan_exit(g: pd.DataFrame, t2_idx: int, p_mid: float, trail: float,
               all_dates: list[str], pos: dict, end_date: str) -> str | None:
    """从建仓后逐日看该股原始收盘：破中位或从峰值回撤>trail → 次日开盘清仓执行日。"""
    close = g["close"].to_numpy(dtype=float)
    dates = g["trade_date"].tolist()
    peak = -1.0
    for k in range(t2_idx + 1, len(g)):               # 建仓日(entry)起监控
        c = close[k]
        if c != c:                                    # 停牌无收盘，跳过
            continue
        peak = max(peak, c)
        if c < p_mid or (peak > 0 and c <= peak * (1.0 - trail)):
            return _next_trade_date(dates[k], all_dates, pos)   # 次日开盘清仓
    return None                                       # 未触发 → 持有到区间末


# ------------------------------------------------------------------ 策略对象
@dataclass
class MarsStrategy:
    max_positions: int
    entries: dict = field(default_factory=dict)       # entry_date -> [(code, exit_date), ...]
    _codes: set = field(default_factory=set)

    def add(self, sig: _Signal):
        self.entries.setdefault(sig.entry_date, []).append((sig.code, sig.exit_date))
        self._codes.add(sig.code)

    def entries_on(self, d: str) -> list[tuple[str, str | None]]:
        return self.entries.get(str(d), [])

    def traded_codes(self) -> list[str]:
        return sorted(self._codes)

    def target_weights(self, T: str) -> pd.Series:    # 协议兼容占位，MARS 走自带 driver
        return pd.Series(dtype=float)


# ------------------------------------------------------------------ 自带回测驱动
def mars_driver(strat: MarsStrategy, market: MarketData, dates: list[str],
                costs: CostModel, cash: float) -> BacktestResult:
    """事件驱动买入持有：每天 = 退市强平 → 执行到期清仓/新建仓 → 逐日盯市。

    与引擎的差别只在"目标"的构造：延续持仓的目标权重= 其当前市值占比（delta≈0 不交易，
    杜绝逐日等权空转），当日到期的清掉，新信号按空槽 1/N 建仓。撮合/成本/约束全复用
    execution.rebalance，与主引擎口径一致。"""
    n = max(1, int(strat.max_positions))
    slot = 1.0 / n
    pf = Portfolio(cash)
    held_exit: dict[str, str | None] = {}             # code -> 计划清仓执行日
    nav_rows, trades_all, turn_rows, exec_w = [], [], [], {}

    for d in dates:
        # 1) 退市/长期无行情强平（复用引擎口径）
        for code in list(pf.units):
            if market.delisted_by(d, code):
                lq = market.last_quote.get(code)
                price = market.adj_close.at[lq, code] if lq else None
                t = liquidate(pf, code, price, d, costs)
                if t:
                    trades_all.append(t)
                held_exit.pop(code, None)

        opens = market.open_prices(d)
        nav_before = pf.value(opens)
        exits_today = {c for c, ex in held_exit.items() if ex == d}
        active = [c for c in pf.units if c not in exits_today]

        # 2) 构造当日目标：延续持仓保持现权重、到期清仓丢弃、空槽纳入新信号
        target: dict[str, float] = {}
        for code in active:
            p = opens.get(code)
            if p is not None and not pd.isna(p) and p > 0 and nav_before > 0:
                target[code] = pf.units[code] * float(p) / nav_before   # ≈现权重 → 不动
        slots = n - len(active)
        new_entries: list[tuple[str, str | None]] = []
        if slots > 0:
            for code, exit_d in strat.entries_on(d):
                if code in pf.units or code in target:
                    continue
                target[code] = slot
                new_entries.append((code, exit_d))
                if len(new_entries) >= slots:
                    break

        if target or exits_today:
            ts = rebalance(pf, pd.Series(target, dtype=float), d, market, costs)
            trades_all.extend(ts)
            if ts:
                traded = sum(t.amount for t in ts)
                turn_rows.append((d, traded / 2.0 / nav_before if nav_before > 0 else 0.0))
                exec_w[d] = pf.weights(opens)
            # 只有真的买进了才登记其计划清仓日（涨停买不进则本信号作废，不追）
            for code, exit_d in new_entries:
                if code in pf.units:
                    held_exit[code] = exit_d
            for code in list(held_exit):              # 已清空的移除
                if code not in pf.units:
                    held_exit.pop(code, None)

        # 3) 逐日盯市
        nav_rows.append((d, pf.value(market.mark_prices(d)), pf.cash))

    nav_df = pd.DataFrame(nav_rows, columns=["trade_date", "nav", "cash"]).set_index("trade_date")
    tdf = pd.DataFrame([t.__dict__ for t in trades_all]) if trades_all else \
        pd.DataFrame(columns=["date", "ts_code", "side", "amount", "cost"])
    turn = (pd.DataFrame(turn_rows, columns=["trade_date", "turnover"])
            .set_index("trade_date")["turnover"] if turn_rows else pd.Series(dtype=float))
    return BacktestResult(nav=nav_df["nav"], cash=nav_df["cash"], trades=tdf,
                          turnover=turn, exec_weights=exec_w)


# ------------------------------------------------------------------ 构建 + 注册
PARAM_SCHEMA = [
    {"key": "max_positions", "type": "int", "label": "最多持仓数", "default": 10},
    {"key": "surge_threshold", "type": "float", "label": "大盘放量阈值(亿)", "default": 600},
    {"key": "pullback_shrink", "type": "float", "label": "回踩缩量比例", "default": 0.8},
    {"key": "trail_stop", "type": "float", "label": "移动止盈回撤", "default": 0.05},
]


def _build(params: dict, ctx: StrategyContext) -> MarsStrategy:
    dates = [str(d) for d in ctx.dates]               # cadence='D' → 全部交易日
    if len(dates) < 3:
        raise ValueError("回测区间过短")
    start, end = dates[0], dates[-1]
    ctx.progress("MARS：解析扫描域", 42)
    codes = _scan_codes(ctx.pool, end)
    if not codes:
        raise ValueError("扫描域为空，请检查股票池")

    warm_start = (datetime.strptime(start, "%Y%m%d") - timedelta(days=WARMUP_DAYS)).strftime("%Y%m%d")
    all_dates = calendar.get_trade_dates(warm_start, end)
    pos = {d: i for i, d in enumerate(all_dates)}
    p = {**params, "_start": start, "_end": end}

    ctx.progress("MARS：读大盘成交额", 46)
    sh = _sh_turnover(warm_start, end)
    surge = float(p["surge_threshold"])
    surge_ok = set(sh.index[(sh.diff() > surge).fillna(False)])

    ctx.progress("MARS：读扫描域行情", 50)
    bars = _load_bars(codes, warm_start, end)
    if bars.empty:
        raise ValueError("扫描域无行情数据")

    ctx.progress("MARS：扫描信号", 58)
    strat = MarsStrategy(max_positions=int(p["max_positions"]))
    for _, g in bars.groupby("ts_code", sort=False):
        for sig in _scan_one(g, surge_ok, all_dates, pos, p):
            strat.add(sig)
    ctx.progress(f"MARS：命中 {len(strat.traded_codes())} 只标的", 62)
    return strat


register_strategy(
    "mars", "MARS 打板回踩",
    "大盘放量>阈值 + 个股封涨停(反包/突破/主升之一) + 回踩两日不破中位且缩量 → T+3开盘建仓，"
    "破中位或峰值回撤止盈清仓。事件驱动买入持有，自带回测驱动。",
    PARAM_SCHEMA, cadence="D", driver=mars_driver,
)(_build)
