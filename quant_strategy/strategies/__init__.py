"""具体策略实现。新增策略只需实现 Strategy 协议（`target_weights(T) -> pd.Series`）
并用 `quant_strategy.base.register_strategy` 注册（见 multifactor.py 末尾），
无需改动引擎、回测服务或前端表单代码。"""
from quant_strategy.strategies import multifactor  # noqa: F401  触发 @register_strategy
from quant_strategy.strategies import mars          # noqa: F401  触发 @register_strategy
