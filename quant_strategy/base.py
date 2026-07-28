"""策略层协议 + 策略注册表。

实现 Strategy 协议 + 用 `register_strategy` 注册即可插入系统，回测服务只认注册表，
不 import 任何具体策略类；新增策略、前端选择框、参数表单均无需改动既有代码。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import pandas as pd


class Combiner(Protocol):
    """多因子合成器：把多个因子的处理后值合成综合得分。"""

    def score(self, T: str, panel: pd.DataFrame, names: list[str]) -> pd.Series:
        """返回 index=ts_code 的综合得分（越大越好）。"""
        ...


class Strategy(Protocol):
    """策略：给定信号日 T，产出目标权重（index=ts_code，和 <= 1）。"""

    def target_weights(self, T: str) -> pd.Series:
        ...


# --------------------------------------------------------------- 策略上下文
@dataclass
class StrategyContext:
    """构建策略所需的公共只读上下文，由回测服务统一准备、所有策略共享。

    get_panel 懒加载因子面板：只有真正用到面板的策略才会触发一次 parquet 读取，
    不用因子面板的策略（如未来的择时/指数增强）零成本跳过。
    """
    dates: list[str]                              # 调仓日（已按 freq 解析）
    pool: object                                   # 股票池 spec（str/dict），策略内部自行 resolve
    get_panel: Callable[[], pd.DataFrame]          # 因子面板懒加载（跨策略共享一次读取）
    progress: Callable[[str, int], None]           # 进度回报 (message, pct 0-100)


# --------------------------------------------------------------- 策略注册表
@dataclass
class StrategySpec:
    id: str
    label: str
    description: str
    param_schema: list[dict]                       # 前端表单渲染用，见各策略模块注释
    build: Callable[[dict, StrategyContext], "Strategy"]


REGISTRY: dict[str, StrategySpec] = {}


def register_strategy(id: str, label: str, description: str, param_schema: list[dict]):
    """装饰构建函数 build(params, ctx) -> Strategy，注册为一个可选策略。"""
    def deco(build_fn):
        REGISTRY[id] = StrategySpec(id, label, description, param_schema, build_fn)
        return build_fn
    return deco


def list_strategies() -> list[dict]:
    return [{"id": s.id, "label": s.label, "description": s.description,
             "param_schema": s.param_schema} for s in REGISTRY.values()]


def default_params(strategy_id: str) -> dict:
    spec = REGISTRY.get(strategy_id)
    if not spec:
        raise ValueError(f"未知策略：{strategy_id}（已注册：{sorted(REGISTRY)}）")
    return {f["key"]: f.get("default") for f in spec.param_schema}


def build(strategy_id: str, params: dict, ctx: StrategyContext) -> "Strategy":
    spec = REGISTRY.get(strategy_id)
    if not spec:
        raise ValueError(f"未知策略：{strategy_id}（已注册：{sorted(REGISTRY)}）")
    merged = {**default_params(strategy_id), **(params or {})}
    return spec.build(merged, ctx)
