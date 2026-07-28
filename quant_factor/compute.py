"""批量计算因子并落地。

流程：逐因子算原始值 → 合并为宽表 → 逐因子中性化 → 处理后宽表 → 落地
data/factor/raw.parquet 与 processed.parquet。面板较小（周频×全市场），整表存储。
"""
from __future__ import annotations

import pandas as pd

import config
from quant_data import storage
from quant_factor import neutralize
from quant_factor.rebalance import get_rebalance_dates
from quant_factor.factors import REGISTRY
from quant_data.log import get_logger

log = get_logger("quant_factor.compute")

KEYS = ["trade_date", "ts_code"]


# --------------------------------------------------------------- 面板 IO
def _panel_path(name: str):
    return config.FACTOR_DIR / f"{name}.parquet"


def write_panel(name: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    config.FACTOR_DIR.mkdir(parents=True, exist_ok=True)
    path = _panel_path(name)
    if path.exists():
        old = pd.read_parquet(path)
        out = pd.concat([storage._antijoin_keep(old, df, KEYS), df], ignore_index=True)
    else:
        out = df
    out = out.sort_values(KEYS).reset_index(drop=True)
    storage._write_parquet(out, path)
    log.info("写入因子面板 %s：%d 行 × %d 列", name, len(out), out.shape[1])
    return len(out)


def read_panel(name: str) -> pd.DataFrame:
    p = _panel_path(name)
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def check_dates_coverage(dates: list[str], panel: pd.DataFrame, min_ratio: float = 0.9) -> None:
    """校验请求的调仓日在因子面板里确有数据，否则直接报错而不是静默跑出错误结果。

    面板是按某个固定频率（如周频）一次性构建的单一文件；若调用方选择的调仓频率
    与构建时不一致（例如面板是周频、请求月频），绝大多数调仓日会在面板里找不到
    对应行——策略/评估会因此几乎不产生交易/样本，却不报任何错，是最隐蔽的一类
    静默错误。命中率低于 min_ratio 时直接拒绝，逼用户要么改频率、要么重建面板。
    """
    dates = {str(d) for d in dates}
    if not dates or panel.empty:
        return
    panel_dates = set(panel["trade_date"].astype(str).unique())
    hit = len(dates & panel_dates)
    ratio = hit / len(dates)
    if ratio < min_ratio:
        raise ValueError(
            f"调仓日与因子面板严重不匹配（{hit}/{len(dates)} = {ratio:.0%} 命中）。"
            f"面板是按固定频率构建的单一文件，当前选择的调仓频率与其不一致，"
            f"继续运行会导致绝大多数调仓日没有因子数据、结果静默失真。"
            f"请把频率改成与当前面板一致，或先在「数据管理→重建因子面板」用目标频率重建。"
        )


# --------------------------------------------------------------- 计算
def compute_factors(dates: list[str], names: list[str] | None = None,
                    do_neutralize: bool = True):
    names = names or list(REGISTRY)
    dates = [str(d) for d in dates]

    raw = None
    for nm in names:
        f = REGISTRY[nm].fn(dates)
        if f.empty:
            log.warning("因子 %s 无数据", nm)
            continue
        f = f.rename(columns={"value": nm})
        raw = f if raw is None else raw.merge(f, on=KEYS, how="outer")
    if raw is None:
        return pd.DataFrame(), pd.DataFrame()
    raw = raw.sort_values(KEYS).reset_index(drop=True)

    proc = raw[KEYS].copy()
    if do_neutralize:
        ind = neutralize.industry_at(dates)
        size = neutralize.lnmktcap_at(dates)
        for nm in names:
            if nm not in raw.columns:
                continue
            fl = raw[KEYS + [nm]].dropna(subset=[nm]).rename(columns={nm: "value"})
            z = neutralize.neutralize_factor(fl, ind, size).rename(columns={"value": nm})
            proc = proc.merge(z, on=KEYS, how="left")
        log.info("中性化完成：%d 个因子", sum(nm in raw.columns for nm in names))
    return raw, proc


def build(start: str = config.START_DATE, end: str | None = None,
          freq: str = config.REBAL_FREQ, names: list[str] | None = None):
    dates = get_rebalance_dates(start, end, freq)
    log.info("计算因子：%d 个调仓日（%s~%s，freq=%s）", len(dates), dates[0], dates[-1], freq)
    raw, proc = compute_factors(dates, names)
    write_panel("raw", raw)
    write_panel("processed", proc)
    return raw, proc
