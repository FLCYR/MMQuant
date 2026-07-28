"""回测报告格式化输出。"""
from __future__ import annotations

from quant_backtest.metrics import Performance


def _pct(x, nd=2):
    return "  n/a" if x != x else f"{x*100:.{nd}f}%"


def format_performance(p: Performance) -> str:
    lines = [
        f"[{p.label}]",
        f"  累计收益 {_pct(p.total_return)}   年化 {_pct(p.ann_return)}   "
        f"年化波动 {_pct(p.ann_vol)}",
        f"  Sharpe {p.sharpe:6.2f}   最大回撤 {_pct(p.max_dd)}   "
        f"Calmar {p.calmar:5.2f}   日胜率 {_pct(p.win_rate)}",
    ]
    if p.excess_ann == p.excess_ann:
        lines.append(f"  超额年化 {_pct(p.excess_ann)}   跟踪误差 {_pct(p.tracking_error)}   "
                     f"信息比率 IR {p.info_ratio:5.2f}")
    if p.turnover_ann == p.turnover_ann:
        lines.append(f"  年化单边换手 {p.turnover_ann:5.1f}x   累计成本/初始资金 {_pct(p.total_cost_pct)}")
    return "\n".join(lines)


def format_yearly(perfs: list[Performance]) -> str:
    years = sorted({y for p in perfs for y in p.yearly})
    if not years:
        return ""
    head = "  年份  " + "".join(f"{p.label:>12s}" for p in perfs)
    rows = [head]
    for y in years:
        row = f"  {y}  " + "".join(f"{_pct(p.yearly.get(y, float('nan'))):>12s}" for p in perfs)
        rows.append(row)
    return "\n".join(rows)


def print_report(perfs: list[Performance], title: str = "回测结果") -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    for p in perfs:
        print(format_performance(p))
        print("-" * 78)
    y = format_yearly(perfs)
    if y:
        print("分年度收益:")
        print(y)
