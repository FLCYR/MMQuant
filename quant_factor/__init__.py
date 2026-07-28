"""A 股量化系统 · 因子层。

从数据层派生横截面选股信号，并验证因子的正确性与有效性。

模块：
- rebalance : 调仓/评估日（默认周频）
- returns   : 复权收益、前向收益面板
- universe  : 每调仓日可投域
- neutralize: 去极值 / 行业+市值中性化 / 标准化
- factors   : 13 个因子（7 大风格）
- compute   : 批量计算并落地因子面板
- evaluate  : IC / 分组 / 多空 有效性评估
"""

__version__ = "0.1.0"
