"""多因子选股策略回测（纯多头，对标中证500）。

    python scripts/run_backtest.py --start 20160101 --end 20241231
    python scripts/run_backtest.py --combiner ic --industry-neutral --topn 100
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse
from datetime import datetime

import config
from quant_backtest import engine, metrics, report
from quant_backtest.costs import CostModel
from quant_backtest.market import MarketData
from quant_factor import compute, evaluate, universe
from quant_factor.rebalance import get_rebalance_dates
from quant_strategy.combine import EqualWeightCombiner, RollingICCombiner
from quant_strategy.strategies.multifactor import MultiFactorStrategy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20160101")
    ap.add_argument("--end", default=None)
    ap.add_argument("--freq", default=config.REBAL_FREQ)
    ap.add_argument("--pool", default=config.UNIV_POOL)
    ap.add_argument("--topn", type=int, default=config.STRATEGY_TOPN)
    ap.add_argument("--factors", nargs="*", default=config.STRATEGY_FACTORS)
    ap.add_argument("--combiner", choices=["equal", "ic"], default="equal")
    ap.add_argument("--industry-neutral", action="store_true")
    ap.add_argument("--max-weight", type=float, default=None)
    ap.add_argument("--cash", type=float, default=config.INIT_CASH)
    args = ap.parse_args()

    end = args.end or datetime.now().strftime("%Y%m%d")
    dates = get_rebalance_dates(args.start, end, args.freq)
    if len(dates) < 3:
        raise SystemExit("调仓日过少")
    print(f"回测区间 {dates[0]}~{dates[-1]}，{len(dates)} 个调仓日（{args.freq}），"
          f"域 {args.pool}，持股 {args.topn}，因子 {args.factors}")

    panel = compute.read_panel("processed")
    if panel.empty:
        raise SystemExit("因子面板为空，请先运行 scripts/build_factors.py")

    # 合成器（可插拔：引擎与策略代码零改动）
    if args.combiner == "ic":
        print("计算历史 IC（滚动 IC 加权用，严格只用过去）……")
        ic = evaluate.ic_frame(args.factors, panel, dates, pool=args.pool)
        combiner = RollingICCombiner(ic)
    else:
        combiner = EqualWeightCombiner()

    print("准备行情与策略……")
    uf = universe.universe_frame(dates, pool=args.pool)
    codes = sorted(uf["ts_code"].unique())
    mkt = MarketData.from_storage(dates[0], end, codes)
    strat = MultiFactorStrategy(panel, dates, combiner, args.factors, args.topn,
                                args.pool, args.industry_neutral, args.max_weight)

    print("回测中（含成本 / 零成本）……")
    res = engine.run(strat.target_weights, mkt, dates, CostModel(), args.cash)
    res0 = engine.run(strat.target_weights, mkt, dates, CostModel(zero=True), args.cash)

    idx = list(res.nav.index)
    bench = metrics.benchmark_nav(idx)
    rf = metrics.load_rf(idx)

    perfs = [
        metrics.summarize(res.nav, "含成本", bench, rf, res.turnover, res.total_cost, args.cash),
        metrics.summarize(res0.nav, "零成本", bench, rf, res0.turnover, 0.0, args.cash),
    ]
    if len(bench):
        perfs.append(metrics.summarize(bench * args.cash, "中证500", None, rf))

    tag = f"{args.combiner}{'+行业中性' if args.industry_neutral else ''}"
    report.print_report(perfs, f"多因子选股回测（{tag}，top{args.topn}，{args.freq}）")
    print(f"\n成交笔数 {len(res.trades)}，累计费用 {res.total_cost:,.0f} 元 "
          f"（占初始资金 {res.total_cost/args.cash:.2%}）")


if __name__ == "__main__":
    main()
