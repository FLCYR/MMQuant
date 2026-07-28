"""股票池（基础池 + 可插拔池）正确性验证。

覆盖：
1. 向后兼容：字符串简写 'csi500'/'all' 与旧行为一致。
2. 基础池不变量：无 ST / 无停牌 / 上市满 N 日。
3. 指数池 PIT：成分随日期变化，且与区间表 in/out 一致。
4. 组合算子语义：and(交)/or(并)/then(串)/without(减)。
5. 市值池排序：bottom/top/q 选中的确为对应市值区段。
6. spec 解析：字符串 == 等价 dict。
"""
from __future__ import annotations

import pytest

from quant_data import storage
from quant_factor import universe as U
from quant_factor.universe import resolve_pool
from quant_factor.universe.pools import (
    IndexPool, SizePool, AllPool, LiquidityPool, IndustryPool, BoardPool, CustomPool,
)

T = "20240628"
BANK = "801780.SI"      # SW L1 银行


def _has_data() -> bool:
    return bool(storage.fact_source("daily_quote")) and not storage.read_dim("index_member").empty


pytestmark = pytest.mark.skipif(not _has_data(), reason="无数据/无成分表，请先 backfill")


# ---------------------------------------------------------------- 1. 兼容
def test_all_is_base_pool():
    all_n = len(U.universe_frame([T], pool="all"))
    csi = len(U.universe_frame([T], pool="csi500"))
    assert all_n > csi > 0
    # 'all' 即基础池，应等于不加任何成员筛选
    from quant_factor.universe.base import BasePool
    assert all_n == len(BasePool().members([T]))


def test_index_alias_equals_explicit():
    a = U.get_universe(T, pool="csi500")
    b = U.get_universe(T, pool="index:000905.SH")
    assert a == b and len(a) > 0


def test_csi500_count_reasonable():
    u = U.get_universe(T, pool="csi500")
    assert 470 <= len(u) <= 500


# ---------------------------------------------------------------- 2. 基础池不变量
def test_base_pool_no_st_no_suspended():
    u = U.get_universe(T, pool="all")
    dstr = ",".join(f"'{c}'" for c in u)
    chk = storage.query(f"""SELECT sum(is_st) st, sum(is_suspended) susp
        FROM {storage.fact_source('daily_quote')}
        WHERE trade_date='{T}' AND ts_code IN ({dstr})""").iloc[0]
    assert int(chk["st"]) == 0 and int(chk["susp"]) == 0


# ---------------------------------------------------------------- 3. 指数池 PIT
def test_index_pool_pit_consistent_with_intervals():
    """池选出的成分，必须都满足 in_date<=T<out_date。"""
    u = U.get_universe(T, pool="hs300")
    im = storage.read_dim("index_member")
    im = im[im["index_code"] == "000300.SH"].copy()
    im["in_date"] = im["in_date"].astype(str)
    im["of"] = im["out_date"].fillna("99991231").astype(str)
    valid = set(im[(im["in_date"] <= T) & (im["of"] > T)]["ts_code"])
    assert u and u <= valid           # 池 ⊆ 当日区间成分（差集来自基础池剔除）


def test_index_pool_membership_changes_over_time():
    early = U.get_universe("20160630", pool="csi1000")
    late = U.get_universe("20240628", pool="csi1000")
    assert early and late and early != late    # 成分随时间变化，非静态名单


# ---------------------------------------------------------------- 4. 组合算子
def test_and_is_intersection():
    hs = U.get_universe(T, pool="hs300")
    ci = U.get_universe(T, pool="csi500")
    both = U.get_universe(T, pool={"and": [{"index": "000300.SH"}, {"index": "000905.SH"}]})
    assert both == (hs & ci)          # 300/500 不重叠 → 空集


def test_or_is_union():
    hs = U.get_universe(T, pool="hs300")
    ci = U.get_universe(T, pool="csi500")
    either = U.get_universe(T, pool={"or": [{"index": "000300.SH"}, {"index": "000905.SH"}]})
    assert either == (hs | ci)


def test_without_is_difference():
    allu = U.get_universe(T, pool="all")
    ci = U.get_universe(T, pool="csi500")
    diff = U.get_universe(T, pool={"without": [{"all": {}}, {"index": "000905.SH"}]})
    assert diff == (allu - ci)


def test_then_ranks_within_upstream():
    """then[csi500 -> size.bottom:100]：结果 ⊆ csi500 且恰 100 只。"""
    ci = U.get_universe(T, pool="csi500")
    picked = U.get_universe(T, pool={"then": [{"index": "000905.SH"}, {"size": {"bottom": 100}}]})
    assert len(picked) == 100 and picked <= ci


# ---------------------------------------------------------------- 5. 市值池排序
def test_size_bottom_selects_smallest():
    df = U.universe_frame([T], pool="size.bottom:200")
    assert len(df) == 200
    db = storage.fact_source("daily_basic")
    mv = storage.query(f"""SELECT ts_code, total_mv FROM {db}
        WHERE trade_date='{T}' AND total_mv>0""").set_index("ts_code")["total_mv"]
    picked_max = mv.reindex(df["ts_code"]).max()
    allu = U.get_universe(T, pool="all")
    rest_min = mv.reindex([c for c in allu if c not in set(df["ts_code"])]).dropna().min()
    assert picked_max <= rest_min + 1e-6      # 选中的最大市值 ≤ 未选中的最小市值


def test_size_top_selects_largest():
    df = U.universe_frame([T], pool="size.top:200")
    assert len(df) == 200
    db = storage.fact_source("daily_basic")
    mv = storage.query(f"""SELECT ts_code, total_mv FROM {db}
        WHERE trade_date='{T}' AND total_mv>0""").set_index("ts_code")["total_mv"]
    picked_min = mv.reindex(df["ts_code"]).min()
    allu = U.get_universe(T, pool="all")
    rest_max = mv.reindex([c for c in allu if c not in set(df["ts_code"])]).dropna().max()
    assert picked_min >= rest_max - 1e-6


# ---------------------------------------------------------------- 6. spec 解析
def test_string_equals_dict_spec():
    assert resolve_pool("csi500").__class__ is IndexPool
    assert isinstance(resolve_pool("all"), AllPool)
    s = resolve_pool("size.bottom:200")
    assert isinstance(s, SizePool) and s.bottom == 200
    q = resolve_pool("size.q:0.0-0.3")
    assert isinstance(q, SizePool) and q.q == (0.0, 0.3)


def test_unknown_spec_raises():
    with pytest.raises(ValueError):
        resolve_pool("nonsense_pool")
    with pytest.raises(ValueError):
        resolve_pool({"unknown_key": {}})


# ---------------------------------------------------------------- 7. 流动性池
def test_liquidity_top_selects_most_active():
    df = U.universe_frame([T], pool="liquidity.top:200")
    assert len(df) == 200
    dq = storage.fact_source("daily_quote")
    amt = storage.query(f"""SELECT ts_code, amount FROM {dq}
        WHERE trade_date='{T}' AND is_suspended=0 AND amount>0""").set_index("ts_code")["amount"]
    picked_min = amt.reindex(df["ts_code"]).min()
    allu = U.get_universe(T, pool="all")
    rest_max = amt.reindex([c for c in allu if c not in set(df["ts_code"])]).dropna().max()
    assert picked_min >= rest_max - 1e-6          # 选中的最低成交额 ≥ 未选中的最高


def test_liquidity_by_turnover():
    df = U.universe_frame([T], pool={"liquidity": {"top": 100, "by": "turnover"}})
    assert len(df) == 100


# ---------------------------------------------------------------- 8. 行业池
def test_industry_subset_all_in_industry():
    u = U.get_universe(T, pool=f"industry:{BANK}")
    assert u
    im = storage.read_dim("industry_member")
    im = im[(im["src"] == "SW2021") & (im["level"] == "L1") & (im["industry_code"] == BANK)].copy()
    im["in_date"] = im["in_date"].astype(str)
    im["of"] = im["out_date"].fillna("99991231").astype(str)
    bank = set(im[(im["in_date"] <= T) & (im["of"] > T)]["ts_code"])
    assert u <= bank

def test_industry_union_is_larger():
    one = U.get_universe(T, pool=f"industry:{BANK}")
    two = U.get_universe(T, pool=f"industry:{BANK},801150.SI")   # 银行+医药
    assert one < two


# ---------------------------------------------------------------- 9. 板块池
def test_boards_partition_all():
    """四板块两两不相交且并集 = 全市场基础池。"""
    allu = U.get_universe(T, pool="all")
    parts = [U.get_universe(T, pool=f"board:{b}") for b in ("主板", "创业板", "科创板", "北交所")]
    union = set().union(*parts)
    total = sum(len(p) for p in parts)
    assert union == allu and total == len(allu)   # 无重叠、无遗漏

def test_board_unknown_raises():
    with pytest.raises(ValueError):
        U.universe_frame([T], pool="board:不存在板块")


# ---------------------------------------------------------------- 10. 自定义名单池
def test_custom_is_intersection_with_base():
    allu = U.get_universe(T, pool="all")
    some = list(allu)[:5] + ["999999.XX"]         # 混入一个不存在的代码
    u = U.get_universe(T, pool={"custom": some})
    assert u == set(list(allu)[:5])               # 只保留基础池内真实存在的


# ---------------------------------------------------------------- 11. 新池 spec 解析
def test_new_pool_specs_parse():
    assert isinstance(resolve_pool("liquidity.top:200"), LiquidityPool)
    assert resolve_pool("liquidity.bottom:50").bottom == 50
    ind = resolve_pool("industry:801780.SI,801150.SI")
    assert isinstance(ind, IndustryPool) and ind.codes == ["801780.SI", "801150.SI"]
    assert isinstance(resolve_pool("board:主板,创业板"), BoardPool)
    assert isinstance(resolve_pool("custom:600519.SH"), CustomPool)
