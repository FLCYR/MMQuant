"""历史行业归属 dim_industry_member（区间表，申万2021）。

index_member_all 直接给出每只股票在 SW L1/L2/L3 的归属及 in_date/out_date，
天然是区间表。按 L1 行业逐个拉取后，将 L1/L2/L3 三级展开为三类记录。
公司会变更行业，用当前分类回溯历史会导致中性化错误，故必须存区间。
"""
from __future__ import annotations

import pandas as pd

import config
from quant_data.client import TushareClient, get_client
from quant_data import storage
from quant_data.log import get_logger

log = get_logger("quant_data.fetchers.industry")

_LEVELS = {
    "L1": ("l1_code", "l1_name"),
    "L2": ("l2_code", "l2_name"),
    "L3": ("l3_code", "l3_name"),
}
MEMBER_FIELDS = ("ts_code,l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,in_date,out_date")


def _close_overlaps(df: pd.DataFrame, group_keys: list[str]) -> pd.DataFrame:
    """消除同一 (ts_code,src,level) 内的区间重叠：把每段 out_date 截断到下一段
    in_date。源数据在行业调整时常新增归属却未关闭旧归属（out_date 为空），
    直接落库会导致同一时点匹配多个行业、join 膨胀。"""
    d = df.sort_values(group_keys + ["in_date"]).reset_index(drop=True)
    next_in = d.groupby(group_keys)["in_date"].shift(-1)
    out = d["out_date"]
    need = next_in.notna() & (out.isna() | (out > next_in))
    d.loc[need, "out_date"] = next_in[need]
    # 截断后若出现 out_date <= in_date（同日重复归属），丢弃空区间
    d = d[d["out_date"].isna() | (d["out_date"] > d["in_date"])]
    return d.reset_index(drop=True)


def sync(client: TushareClient | None = None, src: str = config.SW_INDUSTRY_SRC) -> int:
    client = client or get_client()

    l1 = client.call("index_classify", level="L1", src=src,
                     fields="index_code,industry_name")
    if l1.empty:
        log.warning("index_classify 未返回 L1 行业（src=%s）", src)
        return 0

    frames = []
    for code in l1["index_code"]:
        m = client.call("index_member_all", l1_code=code, fields=MEMBER_FIELDS)
        if not m.empty:
            frames.append(m)
    if not frames:
        log.warning("index_member_all 未返回成分")
        return 0
    allm = pd.concat(frames, ignore_index=True)

    # 三级展开为统一区间记录
    parts = []
    for level, (ccol, ncol) in _LEVELS.items():
        sub = allm[["ts_code", ccol, ncol, "in_date", "out_date"]].rename(
            columns={ccol: "industry_code", ncol: "industry_name"})
        sub = sub.dropna(subset=["industry_code"])
        sub["src"] = src
        sub["level"] = level
        parts.append(sub)
    out = pd.concat(parts, ignore_index=True)
    out = out[["ts_code", "src", "level", "industry_code", "industry_name", "in_date", "out_date"]]
    out["in_date"] = out["in_date"].astype("string")
    out["out_date"] = out["out_date"].astype("string")
    out = out.drop_duplicates(["ts_code", "src", "level", "in_date"]).sort_values(
        ["ts_code", "level", "in_date"]).reset_index(drop=True)
    out = _close_overlaps(out, ["ts_code", "src", "level"])

    # 保留其他来源（如 SW2014）的记录，只替换本 src
    old = storage.read_dim("industry_member")
    if not old.empty:
        old = old[old["src"] != src]
        out = pd.concat([old, out], ignore_index=True)
    n = storage.write_dim("industry_member", out)
    log.info("行业区间同步完成（%s）：%d 条，覆盖 L1 行业 %d 个", src, n, len(l1))
    return n


if __name__ == "__main__":
    sync()
