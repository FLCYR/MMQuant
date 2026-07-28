"""纯逻辑单测（合成数据，不依赖网络/已下载数据）：
区间重建、区间重叠消除、幂等写、PIT SQL 选版逻辑。
"""
from __future__ import annotations

import shutil

import pandas as pd

from quant_data import storage, pit
from quant_data.fetchers.dim_index_member import _build_intervals
from quant_data.fetchers.dim_industry import _close_overlaps


# ---------------- 成分区间重建 ----------------
def test_build_intervals_basic():
    snaps = {"20200131": {"A", "B"}, "20200229": {"A", "C"}, "20200331": {"A", "C"}}
    rows = set(_build_intervals(snaps))
    assert ("A", "20200131", None) in rows        # 一直在
    assert ("B", "20200131", "20200229") in rows   # 0229 被剔除
    assert ("C", "20200229", None) in rows          # 0229 纳入，仍在


def test_build_intervals_reentry():
    snaps = {"20200131": {"A"}, "20200229": set(), "20200331": {"A"}}
    a = {(i, o) for t, i, o in _build_intervals(snaps) if t == "A"}
    assert ("20200131", "20200229") in a            # 第一段
    assert ("20200331", None) in a                  # 再度纳入


# ---------------- 行业区间重叠消除 ----------------
def test_close_overlaps_caps_open_interval():
    df = pd.DataFrame({
        "ts_code": ["X", "X"], "src": ["SW2021", "SW2021"], "level": ["L1", "L1"],
        "industry_code": ["I1", "I2"], "industry_name": ["a", "b"],
        "in_date": ["20200101", "20210101"], "out_date": [None, None],
    })
    out = _close_overlaps(df, ["ts_code", "src", "level"])
    first = out[out["industry_code"] == "I1"].iloc[0]
    assert first["out_date"] == "20210101"          # 旧段被截断到新段 in_date
    last = out[out["industry_code"] == "I2"].iloc[0]
    assert last["out_date"] is None or pd.isna(last["out_date"])


def test_close_overlaps_no_overlap_after():
    from quant_data.checks.rules_interval import c505_no_overlap
    df = pd.DataFrame({
        "ts_code": ["X", "X", "X"], "src": ["SW2021"] * 3, "level": ["L1"] * 3,
        "industry_code": ["I1", "I2", "I3"], "industry_name": ["a", "b", "c"],
        "in_date": ["20200101", "20200601", "20210101"],
        "out_date": [None, None, None],
    })
    out = _close_overlaps(df, ["ts_code", "src", "level"])
    assert c505_no_overlap(out, ["ts_code", "src", "level"]).passed


# ---------------- 幂等写 ----------------
def test_storage_idempotent_write():
    t = "_test_idem_tmp"
    try:
        df = pd.DataFrame({"ts_code": ["A", "B"], "trade_date": ["20200102", "20200102"],
                           "v": [1.0, 2.0]})
        storage.write_fact(t, df, part_col="trade_date", keys=["ts_code", "trade_date"])
        assert len(storage.read_fact(t)) == 2
        # 重写同一天 A 改值 → 覆盖不重复
        df2 = pd.DataFrame({"ts_code": ["A"], "trade_date": ["20200102"], "v": [9.0]})
        storage.write_fact(t, df2, part_col="trade_date", keys=["ts_code", "trade_date"])
        out = storage.read_fact(t)
        assert len(out) == 2
        assert float(out[out["ts_code"] == "A"]["v"].iloc[0]) == 9.0
    finally:
        shutil.rmtree(storage.FACT_DIR / t, ignore_errors=True)


def test_storage_fact_partition_by_year():
    t = "_test_part_tmp"
    try:
        df = pd.DataFrame({"ts_code": ["A", "A"], "trade_date": ["20190105", "20200105"],
                           "v": [1.0, 2.0]})
        storage.write_fact(t, df, part_col="trade_date", keys=["ts_code", "trade_date"])
        assert (storage.FACT_DIR / t / "year=2019" / "data.parquet").exists()
        assert (storage.FACT_DIR / t / "year=2020" / "data.parquet").exists()
    finally:
        shutil.rmtree(storage.FACT_DIR / t, ignore_errors=True)


# ---------------- PIT 选版逻辑（合成 financial 临时表）----------------
def test_pit_selects_latest_visible_version():
    """构造同一报告期的多版本 + 多报告期，验证 PIT 只取 ann_date<=T 的最新版本、最新期。"""
    t = "financial"          # pit 读取 fact_source('financial')
    backup = storage.FACT_DIR / t
    stash = storage.FACT_DIR / (t + "_stash")
    if backup.exists():
        backup.rename(stash)
    try:
        rows = pd.DataFrame([
            # 2022 年报：原始版(ann 20230425) + 修正版(ann 20230820)
            {"ts_code": "T.SH", "end_date": "20221231", "ann_date": "20230425",
             "report_type": "1", "revenue": 100.0, "roe": 10.0},
            {"ts_code": "T.SH", "end_date": "20221231", "ann_date": "20230820",
             "report_type": "1", "revenue": 110.0, "roe": 11.0},   # 修正
            # 2023 一季报（ann 20230428）
            {"ts_code": "T.SH", "end_date": "20230331", "ann_date": "20230428",
             "report_type": "1", "revenue": 30.0, "roe": 3.0},
        ])
        storage.write_fact(t, rows, part_col="end_date",
                           keys=["ts_code", "end_date", "ann_date", "report_type"])

        # T=20230500：一季报可见；年报只有原始版可见（修正版 0820 尚未披露）→ 取最新期=一季报
        p1 = pit.get_financial_pit("20230500")
        assert len(p1) == 1 and p1.iloc[0]["end_date"] == "20230331"

        # T=20230427：一季报(0428)尚不可见 → 最新期=2022年报，版本=原始(revenue 100)
        p2 = pit.get_financial_pit("20230427")
        assert len(p2) == 1
        assert p2.iloc[0]["end_date"] == "20221231"
        assert float(p2.iloc[0]["revenue"]) == 100.0

        # T=20231231：年报取修正版(revenue 110)，但最新期仍是一季报
        p3 = pit.get_financial_pit("20231231")
        assert p3.iloc[0]["end_date"] == "20230331"
        hist = pit.get_financial_history_pit("20231231")
        y2022 = hist[hist["end_date"] == "20221231"].iloc[0]
        assert float(y2022["revenue"]) == 110.0   # 修正版
    finally:
        shutil.rmtree(storage.FACT_DIR / t, ignore_errors=True)
        if stash.exists():
            stash.rename(backup)
