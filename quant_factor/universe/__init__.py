"""股票池：基础池 + 可插拔池。

两层分离：BasePool（恒定可投性硬约束）产出干净可投集合，可插拔 Pool 在其上再筛/组合。
pool spec 三态：字符串简写（'csi500'/'all'/'index:CODE'/'size.bottom:200'）、
dict 组合（{'then': [{'index':'000905.SH'}, {'size':{'bottom':200}}]}）、Pool 对象。

对外入口 universe_frame / get_universe 与旧版签名一致，既有调用点零改动。
导入本包即触发 pools / compose 的池注册。
"""
from quant_factor.universe.base import (  # noqa: F401
    Pool, BasePool, POOL_REGISTRY, register_pool, resolve_pool,
    universe_frame, get_universe, ALIASES,
)
from quant_factor.universe import pools, compose  # noqa: F401  触发 @register_pool
