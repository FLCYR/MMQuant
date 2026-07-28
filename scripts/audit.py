"""数据现状审计报告：各表行数/时间跨度/覆盖率 + 同步日志 + 全量校验结果。

    python scripts/audit.py
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import config
from quant_data import storage, calendar, pit
from quant_data.checks import base, rules_financial, rules_interval, rules_anomaly


def _q(sql):
    return storage.query(sql)


def section(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def footprint():
    import os
    total = 0
    for r, _, fs in os.walk(config.DATA_DIR):
        for f in fs:
            total += os.path.getsize(os.path.join(r, f))
    print(f"data/ 目录占用：{total/1e6:.1f} MB")


def dims():
    section("维度表 dim")
    rows = []
    for name in ["trade_calendar", "stock_basic", "namechange",
                 "index_member", "industry_member", "risk_free_rate"]:
        d = storage.read_dim(name)
        rows.append((name, len(d)))
    for name, n in rows:
        print(f"  {name:18s} {n:>8,} 行")

    sb = storage.read_dim("stock_basic")
    print("  stock_basic 状态分布：", sb["list_status"].value_counts().to_dict())
    im = storage.read_dim("index_member")
    if not im.empty:
        print(f"  index_member 覆盖股票：{im['ts_code'].nunique()} 只，"
              f"区间 {len(im)} 条，in_date {im['in_date'].min()}~{im['in_date'].max()}")
    ind = storage.read_dim("industry_member")
    if not ind.empty:
        print("  industry_member 层级：", ind["level"].value_counts().to_dict())
    rf = storage.read_dim("risk_free_rate")
    if not rf.empty:
        print(f"  risk_free_rate：{rf['trade_date'].min()}~{rf['trade_date'].max()}，"
              f"rate_1y 均值 {rf['rate_1y'].astype(float).mean():.3f}%")


def facts():
    section("事实表 fact")
    for t, ecol, dcol in [("daily_quote", "ts_code", "trade_date"),
                          ("daily_basic", "ts_code", "trade_date"),
                          ("financial", "ts_code", "end_date"),
                          ("index_daily", "index_code", "trade_date")]:
        src = storage.fact_source(t)
        if not src:
            print(f"  {t:14s} (空)")
            continue
        r = _q(f"""SELECT count(*) n, count(DISTINCT {ecol}) ent,
                          count(DISTINCT {dcol}) dts, min({dcol}) mn, max({dcol}) mx
                   FROM {src}""").iloc[0]
        print(f"  {t:14s} {int(r['n']):>10,} 行 | {int(r['ent']):>5} {ecol} | "
              f"{int(r['dts']):>5} {dcol} | {r['mn']}~{r['mx']}")


def quote_quality():
    section("日线行情质量")
    src = storage.fact_source("daily_quote")
    mx = _q(f"SELECT max(trade_date) m FROM {src}").iloc[0]["m"]
    cal = calendar.get_trade_dates(config.START_DATE, mx)   # 截到已有最新日，排除未来交易日
    have = _q(f"SELECT count(DISTINCT trade_date) d FROM {src}").iloc[0]["d"]
    print(f"  交易日覆盖：{int(have)} / {len(cal)}（{config.START_DATE}~{mx} 应有交易日）")
    agg = _q(f"""SELECT
        count(*) nrows, sum(is_suspended) susp, sum(is_st) st,
        sum(CASE WHEN adj_factor IS NULL THEN 1 ELSE 0 END) adj_null,
        sum(CASE WHEN is_suspended=0 AND close IS NULL THEN 1 ELSE 0 END) traded_null
        FROM {src}""").iloc[0]
    print(f"  总行数 {int(agg['nrows']):,} | 停牌行 {int(agg['susp']):,} | "
          f"ST 行 {int(agg['st']):,} | adj_factor 空 {int(agg['adj_null'])} | "
          f"成交却无 close {int(agg['traded_null'])}")
    yr = _q(f"""SELECT substr(trade_date,1,4) y, count(DISTINCT ts_code) stocks,
                       round(count(*)*1.0/count(DISTINCT trade_date),0) avg_per_day
                FROM {src} GROUP BY 1 ORDER BY 1""")
    print("  逐年（不同股票数 / 日均行数）：")
    for _, x in yr.iterrows():
        print(f"    {x['y']}  stocks={int(x['stocks']):>4}  avg/day={int(x['avg_per_day']):>4}")


def sync_status():
    section("同步日志 meta_sync_log")
    df = storage.read_meta("sync_log")
    if df.empty:
        print("  (空)")
        return
    g = df.groupby(["task_name", "status"]).size().reset_index(name="n")
    for task in sorted(df["task_name"].unique()):
        sub = g[g["task_name"] == task]
        parts = ", ".join(f"{r['status']}={r['n']}" for _, r in sub.iterrows())
        print(f"  {task:14s} {parts}")
    failed = df[df["status"] != "SUCCESS"]
    if not failed.empty:
        print(f"\n  非 SUCCESS 记录 {len(failed)} 条（可 run_daily / backfill 续补），样本：")
        print(failed[["task_name", "biz_date", "status", "error_msg"]].head(15).to_string(index=False))
    else:
        print("  全部 SUCCESS，无待补。")


def full_checks():
    section("全量校验")
    # C601
    sus, r601 = rules_anomaly.c601_adj_return_probe()
    print(f"  C601 复权探针（相邻交易日 |收益|>25%）：可疑 {r601.fail_count} 条，passed={r601.passed}")
    if not sus.empty:
        print(sus.head(10).to_string(index=False))
    # C5xx financial
    fin = storage.read_fact("financial")
    if not fin.empty:
        for r in base.run_checks(fin, rules_financial.RULES, "fact_financial", "ALL"):
            print(f"  {r.check_id} {r.level:5s} passed={r.passed} fail={r.fail_count}")
    # 区间
    im = storage.read_dim("index_member")
    if not im.empty:
        month_ends = _month_ends()
        for r in [rules_interval.c506_valid_range(im),
                  rules_interval.c505_no_overlap(im, ["index_code", "ts_code"]),
                  rules_interval.c206_member_count(im, month_ends, 500, config.CSI500)]:
            print(f"  {r.check_id} {r.level:5s} index_member  passed={r.passed} fail={r.fail_count}")
    ind = storage.read_dim("industry_member")
    if not ind.empty:
        for r in [rules_interval.c506_valid_range(ind),
                  rules_interval.c505_no_overlap(ind, ["ts_code", "src", "level"])]:
            print(f"  {r.check_id} {r.level:5s} industry_member passed={r.passed} fail={r.fail_count}")


def _month_ends():
    dates = calendar.get_trade_dates(config.START_DATE)
    bm = {}
    for d in dates:
        bm[d[:6]] = d
    return sorted(bm.values())


def pit_demo():
    section("PIT 抽查")
    for T in ["20180928", "20200630", "20230630"]:
        p = pit.get_financial_pit(T)
        future = int((p["ann_date"] > T).sum()) if not p.empty else 0
        print(f"  get_financial_pit('{T}')：{len(p)} 只股票，ann_date>T 的记录 {future} 条（应为 0）")


def main():
    print("A 股量化数据层 · 现状审计")
    footprint()
    dims()
    facts()
    quote_quality()
    sync_status()
    pit_demo()
    full_checks()
    print("\n审计完成。")


if __name__ == "__main__":
    main()
