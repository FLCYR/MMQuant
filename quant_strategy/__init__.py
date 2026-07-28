"""A 股量化系统 · 策略层。

与回测引擎完全解耦：策略只产出"目标权重"，引擎不关心权重怎么来的。
三个可插拔部件：

- Combiner  合成器：多因子 → 综合得分（等权 z-score / 滚动 IC 加权）
- construct 构建器：得分 → 目标权重（top-N 等权 / 行业中性）
- constraints 约束：权重上限等后处理

组装见 strategies/multifactor.py。新增策略只需实现 `target_weights(T)`。
"""

__version__ = "0.1.0"
