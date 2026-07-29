"""安全回归测试：曾发现并修复的 SQL 注入（MarketData.from_storage）。

漏洞背景：`MarketData.from_storage` 曾把 HTTP 请求里的 start/end 直接用 f-string
拼进 DuckDB SQL；构造 `xxx' OR '1'='1` 之类 payload 可绕过 WHERE 条件、拿到全表
数据。修复：改参数化查询（`?` 占位符）+ 输入格式校验。这里固定住"恶意输入必须
被拒绝、正常输入必须放行"两端。

（另一处历史注入点 `data_service.kline` 连同个股 K 线功能已整体移除，不再需要
对应回归测试。）
"""
from __future__ import annotations

import pytest

from quant_data import storage
from quant_backtest.market import MarketData

LIQUID = "600519.SH"          # 贵州茅台，几乎不停牌，覆盖率高


def _has_data() -> bool:
    return bool(storage.fact_source("daily_quote"))


pytestmark = pytest.mark.skipif(not _has_data(), reason="无数据，请先 backfill")


# ---------------------------------------------------------------- MarketData
def test_market_data_rejects_end_injection():
    with pytest.raises(ValueError):
        MarketData.from_storage("20230101", "20230101' OR '1'='1", [LIQUID])


def test_market_data_rejects_start_injection():
    with pytest.raises(ValueError):
        MarketData.from_storage("20230101' OR '1'='1", "20230105", [LIQUID])


def test_market_data_ts_codes_in_clause_is_safe():
    """恶意字符串混进 ts_codes 列表：应被当作字面量精确匹配（查不到→报无数据），
    而不是被解释成 SQL。"""
    with pytest.raises(RuntimeError):
        MarketData.from_storage("20230101", "20230105", ["600519.SH')) OR 1=1--"])


def test_market_data_accepts_valid_request():
    mkt = MarketData.from_storage("20230101", "20230105", [LIQUID])
    assert mkt.dates[0] >= "20230101" and mkt.dates[-1] <= "20230105"
