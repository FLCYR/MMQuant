"""数据正确性验证（作用于已回补的真实数据）。

分三档：
1. 硬不变量：违反即管道错误，断言严格为 0。
2. 厂商口径特征：异常集中在北交所(.BJ，代码迁移/退市整理期)，断言**非北交所**干净，
   BJ 作为已知问题单独标注（.BJ 不在中证500 池内，且文档本就特殊处理）。
3. 罕见舍入：厂商个位数级别的小瑕疵，断言在很小的上界内（阈值旁注实测值）。

说明：这些测试严格验证"数据管道正确"与"内部/跨表一致"；无法单独证明上游 Tushare
原始数值绝对准确——那需要第二数据源交叉验证（见 test_data_quality 末尾 TODO）。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from quant_data import storage, calendar, pit
import config

# --- 罕见厂商瑕疵的容忍上界（旁注为本次全量数据实测值，留出增长余量）---
TOL_CHANGE_MISMATCH = 20      # 实测 1：change != close-pre_close
TOL_CLOSE_OUT_LIMIT = 60      # 实测 10：close 越界涨跌停
TOL_FK_MISSING = 30           # 实测 6：daily_quote 有而 stock_basic 无的 ts_code
TOL_ADJ_DROP_NONBJ = 20       # 实测 4：非BJ adj_factor 单日跌超 1%
TOL_MV_RELATION = 30          # 实测 15：603882.SH(金域医学)2020-09~10 厂商 float>total 瑕疵
TOL_MV_XTABLE = 5             # 实测 1：300262.SZ 2020-10-19 厂商 total_mv 与股本不一致
SAMPLE_YEARS = ("2016", "2020", "2024")


# ======================================================================
# 1. 结构层：主键唯一 / 关键列非空
# ======================================================================
class TestStructure:
    def test_daily_quote_pk_unique(self, dq, sc):
        assert sc(f"SELECT count(*)-count(DISTINCT (ts_code,trade_date)) FROM {dq}") == 0

    def test_daily_basic_pk_unique(self, db, sc):
        assert sc(f"SELECT count(*)-count(DISTINCT (ts_code,trade_date)) FROM {db}") == 0

    def test_financial_pk_unique(self, fin, sc):
        assert sc(f"SELECT count(*)-count(DISTINCT (ts_code,end_date,ann_date,report_type)) FROM {fin}") == 0

    def test_index_daily_pk_unique(self, idx, sc):
        assert sc(f"SELECT count(*)-count(DISTINCT (index_code,trade_date)) FROM {idx}") == 0

    def test_daily_quote_keys_not_null(self, dq, sc):
        assert sc(f"""SELECT count(*) FROM {dq}
            WHERE ts_code IS NULL OR trade_date IS NULL OR adj_factor IS NULL""") == 0


# ======================================================================
# 2. 行情不变量（成交行）：硬约束
# ======================================================================
class TestQuoteInvariants:
    def test_prices_positive(self, dq, sc):
        assert sc(f"""SELECT count(*) FROM {dq} WHERE is_suspended=0 AND close IS NOT NULL
            AND (open<=0 OR high<=0 OR low<=0 OR close<=0 OR pre_close<=0)""") == 0

    def test_ohlc_relations(self, dq, sc):
        assert sc(f"""SELECT count(*) FROM {dq} WHERE is_suspended=0 AND close IS NOT NULL
            AND (high < greatest(open,close) OR low > least(open,close) OR high < low)""") == 0

    def test_adj_factor_positive(self, dq, sc):
        assert sc(f"SELECT count(*) FROM {dq} WHERE adj_factor<=0") == 0

    def test_vol_amount_nonneg(self, dq, sc):
        assert sc(f"SELECT count(*) FROM {dq} WHERE vol<0 OR amount<0") == 0

    def test_pctchg_consistency(self, dq, sc):
        # pct_chg ≈ (close/pre_close-1)*100，容差 0.1（价格 2 位小数舍入）。实测 0。
        assert sc(f"""SELECT count(*) FROM {dq}
            WHERE is_suspended=0 AND close IS NOT NULL AND pre_close>0
            AND abs(pct_chg-(close/pre_close-1)*100) > 0.1""") == 0

    def test_change_consistency(self, dq, sc):
        n = sc(f"""SELECT count(*) FROM {dq}
            WHERE is_suspended=0 AND close IS NOT NULL AND pre_close>0
            AND abs(change-(close-pre_close)) > 0.05""")
        assert n <= TOL_CHANGE_MISMATCH, f"change 与 close-pre_close 不符 {n} 行"

    def test_close_within_limits(self, dq, sc):
        n = sc(f"""SELECT count(*) FROM {dq}
            WHERE is_suspended=0 AND close IS NOT NULL
            AND limit_up IS NOT NULL AND limit_down IS NOT NULL
            AND (close < limit_down-1e-6 OR close > limit_up+1e-6)""")
        assert n <= TOL_CLOSE_OUT_LIMIT, f"close 越界涨跌停 {n} 行"


# ======================================================================
# 3. 复权正确性
# ======================================================================
class TestAdjustment:
    def test_c601_no_adjustment_jumps(self):
        # 后复权价相邻交易日 |收益|>25%（排除 .BJ 与上市 20 日内）应为 0。
        from quant_data.checks import rules_anomaly
        sus, res = rules_anomaly.c601_adj_return_probe()
        assert res.fail_count == 0, f"C601 复权探针命中 {res.fail_count} 条：\n{sus.head()}"

    def test_adj_factor_monotonic_nonbj(self, dq, sc):
        # 后复权因子只增不减。跌幅>1% 的非BJ对应≤实测 4（罕见特殊事件）；
        # 大幅非单调集中在 .BJ 代码迁移，单独在 test_bj_known_issues 记录。
        n = sc(f"""
            WITH r AS (SELECT ts_code, adj_factor,
                LAG(adj_factor) OVER (PARTITION BY ts_code ORDER BY trade_date) prev
                FROM {dq} WHERE is_suspended=0 AND close IS NOT NULL AND ts_code NOT LIKE '%.BJ')
            SELECT count(*) FROM r WHERE prev IS NOT NULL AND adj_factor < prev*0.99""")
        assert n <= TOL_ADJ_DROP_NONBJ, f"非BJ adj_factor 大幅下降 {n} 对"


# ======================================================================
# 4. 估值 / 市值：硬约束 + 跨表一致
# ======================================================================
class TestDailyBasic:
    def test_mv_relation(self, db, sc):
        # total_mv>=circ_mv>0。极少数厂商瑕疵（金域医学 float>total）容忍在上界内。
        n = sc(f"""SELECT count(*) FROM {db}
            WHERE total_mv IS NOT NULL AND circ_mv IS NOT NULL
            AND NOT (total_mv>=circ_mv AND circ_mv>0)""")
        assert n <= TOL_MV_RELATION, f"total_mv<circ_mv 或 circ_mv<=0 的行数 {n}"

    def test_pb_positive(self, db, sc):
        # PB 为负通常是数据错误。实测 0。
        assert sc(f"SELECT count(*) FROM {db} WHERE pb IS NOT NULL AND pb<=0") == 0

    def test_marketcap_cross_table(self, dq, db, sc):
        # total_mv(万元) ≈ close(元)*total_share(万股)，相对误差<1%。抽样年份，实测 0。
        years = " OR ".join(f"q.trade_date LIKE '{y}%'" for y in SAMPLE_YEARS)
        n = sc(f"""
            WITH j AS (SELECT q.close, b.total_share, b.total_mv
                FROM {dq} q JOIN {db} b ON q.ts_code=b.ts_code AND q.trade_date=b.trade_date
                WHERE ({years}) AND q.is_suspended=0 AND q.close>0
                      AND b.total_share>0 AND b.total_mv>0)
            SELECT count(*) FROM j WHERE abs(total_mv-close*total_share)/total_mv > 0.01""")
        assert n <= TOL_MV_XTABLE, f"市值 != 价×股本 的行数 {n}"


# ======================================================================
# 5. 覆盖率 / 外键
# ======================================================================
class TestCoverage:
    def test_calendar_full_coverage(self, dq, sc):
        mx = sc(f"SELECT max(trade_date) FROM {dq}")
        expect = set(calendar.get_trade_dates(config.START_DATE, mx))
        have = set(storage.query(f"SELECT DISTINCT trade_date FROM {dq}")["trade_date"])
        missing = expect - have
        assert not missing, f"缺失交易日 {len(missing)} 个，如 {sorted(missing)[:5]}"

    def test_fk_ts_code_in_stock_basic(self, dq, stock_basic_path, sc):
        n = sc(f"""SELECT count(DISTINCT q.ts_code) FROM {dq} q
            LEFT JOIN read_parquet('{stock_basic_path}') s ON q.ts_code=s.ts_code
            WHERE s.ts_code IS NULL""")
        assert n <= TOL_FK_MISSING, f"{n} 个 ts_code 不在 stock_basic"

    def test_nonbj_quotes_within_listing(self, dq, stock_basic_path, sc):
        # 非BJ：行情日不应早于上市日。实测 0（上市前行情 100% 是 .BJ 代码迁移）。
        n = sc(f"""SELECT count(*) FROM {dq} q
            JOIN read_parquet('{stock_basic_path}') s ON q.ts_code=s.ts_code
            WHERE q.ts_code NOT LIKE '%.BJ' AND q.trade_date < s.list_date""")
        assert n == 0, f"非BJ 上市前行情 {n} 行"

    def test_all_benchmark_indices_present(self, idx, sc):
        got = set(storage.query(f"SELECT DISTINCT index_code FROM {idx}")["index_code"])
        missing = set(config.BENCHMARK_INDICES) - got
        assert not missing, f"缺失指数 {missing}"

    def test_risk_free_full_coverage(self, dq):
        rf = storage.read_dim("risk_free_rate")
        assert not rf.empty and rf["rate_1y"].isna().sum() == 0
        mx = storage.query(f"SELECT max(trade_date) m FROM {dq}").iloc[0]["m"]
        need = set(calendar.get_trade_dates(config.START_DATE, mx))
        assert need - set(rf["trade_date"].astype(str)) == set(), "risk_free 未覆盖全部交易日"

    def test_csi500_members_have_quotes(self, dq, sc):
        # 抽样若干月末，中证500 当日成分股都应有行情。
        im = storage.read_dim("index_member")
        if im.empty:
            pytest.skip("index_member 无数据")
        im = im[im["index_code"] == config.CSI500].copy()
        im["out_fill"] = im["out_date"].fillna("99991231")
        for T in ["20180629", "20200630", "20230630"]:
            members = set(im[(im["in_date"] <= T) & (im["out_fill"] > T)]["ts_code"])
            if not members:
                continue
            have = set(storage.query(
                f"SELECT DISTINCT ts_code FROM {dq} WHERE trade_date='{T}'")["ts_code"])
            missing = members - have
            # 成分股当日可能停牌 → 停牌行 is_suspended=1 仍在表中，故应几乎全覆盖
            assert len(missing) <= 2, f"{T} 有 {len(missing)} 只成分股无行情：{list(missing)[:5]}"


# ======================================================================
# 6. PIT 时点正确性（防未来函数）
# ======================================================================
class TestPIT:
    TS = ["20180928", "20200630", "20230630", "20240628"]

    @pytest.mark.parametrize("T", TS)
    def test_no_future_leak(self, T):
        p = pit.get_financial_pit(T)
        if p.empty:
            pytest.skip("financial 无数据")
        assert (p["ann_date"] > T).sum() == 0, f"{T}: 出现未来 ann_date"

    @pytest.mark.parametrize("T", TS)
    def test_one_row_per_stock(self, T):
        p = pit.get_financial_pit(T)
        if p.empty:
            pytest.skip("financial 无数据")
        assert p["ts_code"].is_unique

    def test_returns_latest_visible_period(self, fin):
        # PIT 返回的 (ts_code,end_date) 应等于该时点可见的最新报告期。
        T = "20230630"
        p = pit.get_financial_pit(T)[["ts_code", "end_date"]]
        ref = storage.query(f"""SELECT ts_code, max(end_date) end_date FROM {fin}
            WHERE ann_date<='{T}' AND report_type='1' GROUP BY ts_code""")
        m = p.merge(ref, on="ts_code", suffixes=("_pit", "_ref"))
        assert (m["end_date_pit"] == m["end_date_ref"]).all()

    def test_ann_ge_end(self, fin, sc):
        assert sc(f"SELECT count(*) FROM {fin} WHERE ann_date < end_date") == 0

    def test_ann_not_future(self, fin, sc):
        today = datetime.now().strftime("%Y%m%d")
        assert sc(f"SELECT count(*) FROM {fin} WHERE ann_date > '{today}'") == 0


# ======================================================================
# 7. 区间表（成分 / 行业）
# ======================================================================
class TestIntervals:
    def _im(self):
        d = storage.read_dim("index_member")
        if d.empty:
            pytest.skip("index_member 无数据")
        return d

    def _ind(self):
        d = storage.read_dim("industry_member")
        if d.empty:
            pytest.skip("industry_member 无数据")
        return d

    def test_index_member_valid_range(self):
        from quant_data.checks.rules_interval import c506_valid_range
        assert c506_valid_range(self._im()).passed

    def test_index_member_no_overlap(self):
        from quant_data.checks.rules_interval import c505_no_overlap
        assert c505_no_overlap(self._im(), ["index_code", "ts_code"]).passed

    def test_industry_no_overlap(self):
        from quant_data.checks.rules_interval import c505_no_overlap
        assert c505_no_overlap(self._ind(), ["ts_code", "src", "level"]).passed

    def test_csi500_member_count(self):
        from quant_data.checks.rules_interval import c206_member_count
        im = self._im()
        dates = calendar.get_trade_dates("20230101", "20241231")
        bm = {}
        for d in dates:
            bm[d[:6]] = d          # 每月覆盖，末值即月末交易日
        month_ends = sorted(bm.values())
        r = c206_member_count(im, month_ends, 500, config.CSI500)
        assert r.passed, f"中证500 成分数偏离 500：{r.sample}"


# ======================================================================
# 8. 交易日历真值（独立于厂商的公开事实）
# ======================================================================
class TestCalendarGroundTruth:
    def _open(self, date):
        cal = storage.read_dim("trade_calendar")
        row = cal[(cal["exchange"] == "SSE") & (cal["cal_date"].astype(str) == date)]
        return None if row.empty else int(row.iloc[0]["is_open"])

    @pytest.mark.parametrize("holiday", ["20200101", "20201001", "20240212", "20190501"])
    def test_known_holidays_closed(self, holiday):
        assert self._open(holiday) == 0, f"{holiday} 应为休市"

    @pytest.mark.parametrize("day", ["20240102", "20231229", "20200703"])
    def test_known_trading_days_open(self, day):
        assert self._open(day) == 1, f"{day} 应为交易日"


# ======================================================================
# 9. 北交所已知口径问题（记录用，非失败）——xfail 明示
# ======================================================================
@pytest.mark.xfail(reason=".BJ 代码迁移导致 adj_factor 重置/上市前行情，为厂商已知问题；"
                          "北交所不在中证500 池内，复权/覆盖检查均排除 .BJ", strict=False)
def test_bj_known_adj_issues(dq, sc):
    n = sc(f"""
        WITH r AS (SELECT ts_code, adj_factor,
            LAG(adj_factor) OVER (PARTITION BY ts_code ORDER BY trade_date) prev
            FROM {dq} WHERE is_suspended=0 AND close IS NOT NULL AND ts_code LIKE '%.BJ')
        SELECT count(*) FROM r WHERE prev IS NOT NULL AND adj_factor < prev*0.99""")
    assert n == 0  # 预期失败：BJ 确有非单调
