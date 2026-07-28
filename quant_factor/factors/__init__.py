"""因子库：13 个因子 / 7 大风格。导入各风格模块以完成注册。"""
from quant_factor.factors import (  # noqa: F401  触发 @register
    value, size, momentum, volatility, liquidity, quality, growth,
)
from quant_factor.factors.base import REGISTRY, Factor, register  # noqa: F401
