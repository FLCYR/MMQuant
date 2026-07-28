"""pytest 公共配置：项目路径、DuckDB 查询辅助、数据源 fixture。"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quant_data import storage  # noqa: E402


def scalar(sql: str):
    """执行 SQL 返回首行首列。"""
    return storage.query(sql).iloc[0, 0]


def _fact(table: str) -> str:
    src = storage.fact_source(table)
    if not src:
        pytest.skip(f"{table} 无数据，请先运行 scripts/backfill.py")
    return src


@pytest.fixture(scope="session")
def q():
    return storage.query


@pytest.fixture(scope="session")
def sc():
    return scalar


@pytest.fixture(scope="session")
def dq():
    return _fact("daily_quote")


@pytest.fixture(scope="session")
def db():
    return _fact("daily_basic")


@pytest.fixture(scope="session")
def fin():
    return _fact("financial")


@pytest.fixture(scope="session")
def idx():
    return _fact("index_daily")


@pytest.fixture(scope="session")
def stock_basic_path():
    p = storage.dim_path("stock_basic")
    if not p.exists():
        pytest.skip("stock_basic 无数据")
    return str(p).replace("\\", "/")
