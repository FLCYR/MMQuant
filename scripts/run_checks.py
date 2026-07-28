"""离线全量校验（建议每月跑一遍）：C601 复权探针 + C5xx 财务 PIT +
区间表 C505/C506/C206。结果写入 meta_check_result 并打印摘要。
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import config
from quant_data import storage, calendar
from quant_data.checks import base, rules_financial, rules_interval, rules_anomaly
from quant_data.log import get_logger

log = get_logger("run_checks")


def check_financial():
    df = storage.read_fact("financial")
    if df.empty:
        log.info("fact_financial 为空，跳过 C5xx")
        return []
    res = base.run_checks(df, rules_financial.RULES, "fact_financial", "ALL")
    base.persist(res)
    base.log_failures(res)
    return res


def check_intervals():
    res = []
    im = storage.read_dim("index_member")
    if not im.empty:
        r1 = rules_interval.c506_valid_range(im)
        r2 = rules_interval.c505_no_overlap(im, ["index_code", "ts_code"])
        # C206：用 2015 至今每个月末交易日抽查中证500 成分数
        month_ends = _month_end_trade_days()
        r3 = rules_interval.c206_member_count(im, month_ends, 500, config.CSI500)
        for r in (r1, r2, r3):
            r.table_name = "dim_index_member"
        res += [r1, r2, r3]

    ind = storage.read_dim("industry_member")
    if not ind.empty:
        r4 = rules_interval.c506_valid_range(ind)
        r5 = rules_interval.c505_no_overlap(ind, ["ts_code", "src", "level"])
        for r in (r4, r5):
            r.table_name = "dim_industry_member"
        res += [r4, r5]

    base.persist(res)
    base.log_failures(res)
    return res


def _month_end_trade_days():
    dates = calendar.get_trade_dates(config.START_DATE)
    by_month = {}
    for d in dates:
        by_month[d[:6]] = d      # 覆盖，最后一个即月末交易日
    return sorted(by_month.values())


def check_c601():
    sus, res = rules_anomaly.c601_adj_return_probe()
    base.persist([res])
    log.info("C601 可疑复权跳变 %d 条（passed=%s）", res.fail_count, res.passed)
    if not sus.empty:
        log.warning("C601 样本：\n%s", sus.head(30).to_string(index=False))
    return sus


def main():
    log.info("===== 离线全量校验 =====")
    fin = check_financial()
    itv = check_intervals()
    check_c601()

    log.info("---- 校验摘要 ----")
    for r in fin + itv:
        flag = "OK" if r.passed else f"FAIL({r.fail_count})"
        log.info("%-5s %-22s %s", r.check_id, r.table_name, flag)


if __name__ == "__main__":
    main()
