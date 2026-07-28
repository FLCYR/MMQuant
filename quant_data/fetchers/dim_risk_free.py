"""无风险利率 dim_risk_free_rate。

优先级降级链（2000 积分账户通常无 yc_cb 权限）：
1. yc_cb  —— 中债国债到期收益率曲线，取 1 年/10 年期（最规范）。
2. shibor —— SHIBOR，取 1 年期作为 rate_1y（免费，常可用）。
3. 常量   —— 用 config.RISK_FREE_DEFAULT_ANNUAL 按交易日历铺满，保证下游夏普可算。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

import config
from quant_data.client import TushareClient, get_client
from quant_data import storage, calendar
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.risk_free")


def _from_yc_cb(client, start, end):
    frames = []
    for y in range(int(start[:4]), int(end[:4]) + 1):
        ys, ye = max(f"{y}0101", start), min(f"{y}1231", end)
        try:
            df = client.call("yc_cb", start_date=ys, end_date=ye)
        except Exception as e:
            log.warning("yc_cb %s~%s 失败：%s", ys, ye, e)
            return pd.DataFrame()          # 无权限：整体放弃，走下一降级
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.lower() for c in df.columns]
    if "curve_name" in df.columns:
        m = df["curve_name"].astype(str).str.contains("国债", na=False)
        df = df[m] if m.any() else df
    if "yield_type" in df.columns:
        m = df["yield_type"].astype(str).str.contains("到期", na=False)
        df = df[m] if m.any() else df
    if not {"curve_term", "yield", "trade_date"} <= set(df.columns):
        return pd.DataFrame()

    def pick(term):
        s = df[(df["curve_term"].astype(float) - term).abs() < 1e-6]
        return s.set_index("trade_date")["yield"]

    out = pd.DataFrame({"rate_1y": pick(1.0), "rate_10y": pick(10.0)}).reset_index()
    return out.rename(columns={"index": "trade_date"})


def _from_shibor(client, start, end):
    try:
        df = client.call("shibor", start_date=start, end_date=end)
    except Exception as e:
        log.warning("shibor 失败：%s", e)
        return pd.DataFrame()
    if df.empty or "1y" not in df.columns:
        return pd.DataFrame()
    out = df.rename(columns={"date": "trade_date", "1y": "rate_1y"})[["trade_date", "rate_1y"]]
    out["rate_10y"] = pd.NA
    return out


def _constant(start, end):
    dates = calendar.get_trade_dates(start, end)
    return pd.DataFrame({
        "trade_date": dates,
        "rate_1y": config.RISK_FREE_DEFAULT_ANNUAL,
        "rate_10y": pd.NA,
    })


def sync(client: TushareClient | None = None,
         start: str = config.START_DATE, end: str | None = None) -> int:
    client = client or get_client()
    end = end or datetime.now().strftime("%Y%m%d")

    out = _from_yc_cb(client, start, end)
    source = "yc_cb"
    if out.empty:
        out = _from_shibor(client, start, end)
        source = "shibor"
    if out.empty:
        out = _constant(start, end)
        source = "constant"

    out["trade_date"] = out["trade_date"].astype(str)
    for c in ("rate_1y", "rate_10y"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    # 对齐到完整交易日历：节假日缺口前向填充，数据起点之前用常量兜底
    dates = calendar.get_trade_dates(start, end)
    full = pd.DataFrame({"trade_date": dates})
    out = full.merge(out, on="trade_date", how="left").sort_values("trade_date")
    out["rate_1y"] = out["rate_1y"].ffill().fillna(config.RISK_FREE_DEFAULT_ANNUAL)
    if "rate_10y" in out.columns:
        out["rate_10y"] = out["rate_10y"].ffill()

    n = storage.write_dim("risk_free_rate", out.reset_index(drop=True))
    log.info("无风险利率同步完成：%d 行（来源：%s，已对齐交易日历并兜底）", n, source)
    return n


if __name__ == "__main__":
    sync()
