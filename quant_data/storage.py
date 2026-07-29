"""Parquet 落地 + DuckDB 查询。

- 维度表 dim：全量覆盖写单个 parquet。
- 事实表 fact：按日期列的年份分区，主键幂等写（重复写同一天结果不变）。
- 元数据表 meta：按主键 upsert。
- 隔离区 isolation / 原始留档 raw：按业务日期落文件。
- query(sql)：DuckDB 直接对 parquet 执行 SQL（PIT / 校验用）。

约定：所有日期列统一存 'YYYYMMDD' 字符串（Tushare 原生格式），
零填充后字典序 == 时间序，可直接做 <= / >= 比较，避免时区与类型转换坑。
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

import config
from quant_data.log import get_logger

log = get_logger("quant_data.storage")

DIM_DIR = config.DATA_DIR / "dim"
FACT_DIR = config.DATA_DIR / "fact"
META_DIR = config.DATA_DIR / "meta"
RAW_DIR = config.DATA_DIR / "raw"
ISO_DIR = config.DATA_DIR / "isolation"

for _d in (DIM_DIR, FACT_DIR, META_DIR, RAW_DIR, ISO_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------- 底层写
def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    """原子写：先写 .tmp 再替换，避免中断产生半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _antijoin_keep(old: pd.DataFrame, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """返回 old 中主键不在 new 里的行（用于幂等覆盖）。"""
    old = old.reset_index(drop=True)
    ind = old.merge(new[keys].drop_duplicates(), on=keys, how="left", indicator=True)
    mask = ind["_merge"].eq("left_only").to_numpy()
    return old[mask]


# ----------------------------------------------------------------- 维度表
def write_dim(name: str, df: pd.DataFrame) -> int:
    _write_parquet(df, DIM_DIR / f"{name}.parquet")
    log.info("写入维度表 %s：%d 行", name, len(df))
    return len(df)


def read_dim(name: str) -> pd.DataFrame:
    p = DIM_DIR / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def dim_path(name: str) -> Path:
    return DIM_DIR / f"{name}.parquet"


# ----------------------------------------------------------------- 事实表
def _fact_partition(table: str, year: str) -> Path:
    return FACT_DIR / table / f"year={year}" / "data.parquet"


def write_fact(table: str, df: pd.DataFrame, part_col: str, keys: list[str]) -> int:
    """按 part_col 年份分区、按 keys 幂等写入。

    对每个年份分区：删除主键与新数据重合的旧行，再并入新行，覆盖回写。
    """
    if df is None or df.empty:
        return 0
    df = df.reset_index(drop=True)
    year_ser = df[part_col].astype(str).str[:4]
    written = 0
    for year, idx in df.groupby(year_ser).groups.items():
        g = df.loc[idx]
        path = _fact_partition(table, str(year))
        if path.exists():
            old = pd.read_parquet(path)
            out = pd.concat([_antijoin_keep(old, g, keys), g], ignore_index=True)
        else:
            out = g
        out = out.sort_values(keys).reset_index(drop=True)
        _write_parquet(out, path)
        written += len(g)
    log.info("写入事实表 %s：%d 行", table, written)
    return written


def fact_files(table: str) -> list[Path]:
    base = FACT_DIR / table
    return sorted(base.glob("year=*/*.parquet")) if base.exists() else []


def fact_source(table: str) -> str:
    """返回可用于 DuckDB FROM 的 read_parquet 表达式；无文件时返回空。"""
    files = fact_files(table)
    if not files:
        return ""
    glob = str(FACT_DIR / table / "year=*" / "*.parquet").replace("\\", "/")
    return f"read_parquet('{glob}', hive_partitioning=true, union_by_name=true)"


def read_fact(table: str, columns: str = "*", where: str | None = None) -> pd.DataFrame:
    src = fact_source(table)
    if not src:
        return pd.DataFrame()
    sql = f"SELECT {columns} FROM {src}"
    if where:
        sql += f" WHERE {where}"
    return query(sql)


# ----------------------------------------------------------------- 元数据表
def write_meta(name: str, df: pd.DataFrame, keys: list[str]) -> None:
    if df is None or df.empty:
        return
    path = META_DIR / f"{name}.parquet"
    if path.exists():
        old = pd.read_parquet(path)
        out = pd.concat([_antijoin_keep(old, df, keys), df], ignore_index=True)
    else:
        out = df
    _write_parquet(out, path)


def read_meta(name: str) -> pd.DataFrame:
    p = META_DIR / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


# ----------------------------------------------------------------- 隔离区 / 原始留档
def write_isolation(table: str, biz_date: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    _write_parquet(df, ISO_DIR / table / f"{biz_date}.parquet")
    log.warning("数据写入隔离区：%s / %s（%d 行）", table, biz_date, len(df))


def write_raw(api_name: str, biz_date: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    _write_parquet(df, RAW_DIR / api_name / f"{biz_date}.parquet")


# ----------------------------------------------------------------- DuckDB 查询
def query(sql: str, params: list | None = None) -> pd.DataFrame:
    """执行 SQL。凡拼入 SQL 的值来自外部输入（HTTP 参数等），一律走 params（`?`
    占位符），不得用 f-string 直接拼接——历史上 kline/MarketData 曾因此可注入。"""
    con = duckdb.connect()
    try:
        # 显式限内存/线程，避免默认"按系统总内存的80%"在并发/共享主机上叠加触发 OOM
        con.execute(f"PRAGMA memory_limit='{config.DUCKDB_MEMORY_LIMIT}'")
        con.execute(f"PRAGMA threads={config.DUCKDB_THREADS}")
        return (con.execute(sql, params) if params is not None else con.execute(sql)).df()
    finally:
        con.close()
