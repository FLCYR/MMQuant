"""A 股量化系统 · Web 层（Flask API）。

**独立新增层**：只 import 数据层/因子层/策略层/回测层，不修改它们的任何文件。
所有产物写入 data/backtest/，不触碰既有数据目录结构。

模块：
- store      回测结果落盘与检索（data/backtest/{run_id}/）
- jobs       异步任务（回测耗时数分钟，不能同步返回）
- serialize  DataFrame → JSON（NaN 处理、降采样、分页）
- services   业务逻辑，调用现有包
- api        Flask 蓝图（路由层，薄）
"""

__version__ = "0.1.0"
