"""计算并落地全历史因子面板（原始 + 处理后）。

    python scripts/build_factors.py                     # 全历史周频
    python scripts/build_factors.py --start 20200101 --freq M
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import argparse

import config
from quant_factor import compute
from quant_data import storage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=config.START_DATE)
    ap.add_argument("--end", default=None)
    ap.add_argument("--freq", default=config.REBAL_FREQ)
    args = ap.parse_args()

    raw, proc = compute.build(args.start, args.end, args.freq)
    print(f"原始面板 {raw.shape}，处理后面板 {proc.shape}")
    fac_cols = [c for c in raw.columns if c not in ("trade_date", "ts_code")]
    print("覆盖调仓日:", raw["trade_date"].nunique(),
          "| 时间跨度:", raw["trade_date"].min(), "~", raw["trade_date"].max())
    print("各因子处理后非空率:")
    for c in fac_cols:
        print(f"  {c:7s} {proc[c].notna().mean():.1%}")


if __name__ == "__main__":
    main()
