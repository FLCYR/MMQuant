"""A 股量化系统 · 数据层包。

模块划分：
- client   : Tushare 调用封装（限流 + 重试 + 日志）
- storage  : Parquet 落地 + DuckDB 查询
- calendar : 交易日历工具
- synclog  : 同步日志（增量与失败回补依据）
- pit      : 财务数据 Point-in-Time 访问
- fetchers : 各数据源采集
- checks   : 数据校验
"""

__version__ = "0.1.0"
