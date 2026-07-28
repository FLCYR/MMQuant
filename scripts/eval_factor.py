"""单因子/全因子有效性报告。

    python scripts/eval_factor.py --factor BP
    python scripts/eval_factor.py --factor all --start 20160101 --end 20231231
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse

import numpy as np

import config
from quant_factor import compute, evaluate
from quant_factor.rebalance import get_rebalance_dates
from quant_factor.factors import REGISTRY


def _fmt(r: evaluate.FactorReport) -> str:
    q = " ".join(f"{x:+.3f}" for x in r.quantile_returns)
    dec = " ".join(f"{k}:{v:+.3f}" for k, v in r.decay.items())
    return (f"{r.name:7s} {r.style:10s} dir={r.direction:+d} | "
            f"RankIC={r.rankic_mean:+.4f} ICIR={r.rankic_ir:+.2f} t={r.rankic_t:+.1f} "
            f"pos={r.pos_ratio:.0%} | LS年化={r.ls_ann:+.1%} Sharpe={r.ls_sharpe:+.2f} "
            f"MDD={r.ls_maxdd:.1%} 换手={r.ls_turnover:.0%} | [{r.verdict()}]\n"
            f"        分组(升序): {q}\n        IC衰减: {dec}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--factor", default="all", help="因子名或 all")
    ap.add_argument("--start", default=config.START_DATE)
    ap.add_argument("--end", default=None)
    ap.add_argument("--freq", default=config.REBAL_FREQ)
    ap.add_argument("--pool", default=config.UNIV_POOL)
    args = ap.parse_args()

    names = list(REGISTRY) if args.factor == "all" else [args.factor]
    for nm in names:
        if nm not in REGISTRY:
            raise SystemExit(f"未知因子 {nm}，可选：{list(REGISTRY)}")

    dates = get_rebalance_dates(args.start, args.end, args.freq)
    print(f"评估区间 {dates[0]}~{dates[-1]}，{len(dates)} 个调仓日，频率 {args.freq}，域 {args.pool}")
    print("计算因子中……")
    _, proc = compute.compute_factors(dates, names)

    print("\n" + "=" * 90)
    reports = []
    for nm in names:
        if nm not in proc.columns:
            print(f"{nm}: 无数据")
            continue
        r = evaluate.evaluate_factor(nm, proc, dates, pool=args.pool, freq=args.freq)
        reports.append(r)
    reports.sort(key=lambda r: -abs(r.rankic_mean))
    for r in reports:
        print(_fmt(r))
        print("-" * 90)

    if len(names) > 1:
        print("\n因子相关性矩阵（处理后，平均横截面相关）:")
        corr = evaluate.factor_correlation(proc, [r.name for r in reports])
        with np.errstate(all="ignore"):
            print(corr.round(2).to_string())


if __name__ == "__main__":
    main()
