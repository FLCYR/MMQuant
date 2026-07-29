# 阶段记录

按里程碑记录，非逐提交流水账。每个阶段完成时的详细结论见对应的 auto-memory 记录（如有）。

## 阶段 1：数据层（2026-07-23 ~ 07-24）

- 完整实现采集→校验→存储管道：`quant_data/`（client 限流重试、storage 幂等写、
  calendar、pit、fetchers、checks C1xx~C6xx）
- 设计文档：[A股量化数据层开发文档.md](A股量化数据层开发文档.md)
- 已知数据边界：复权/覆盖异常集中在北交所 `.BJ`（已在检查中排除，且不在中证500 域内）；
  另有 2 处厂商字段瑕疵（603882、300262）已在测试中标注容忍

## 阶段 2：因子层（2026-07-24）

- 13 个因子 / 7 大风格全自研实现（`quant_factor/factors/`），去极值(MAD) → 行业+市值中性化(OLS) →
  标准化(zscore)
- 财务因子一律走 PIT，杜绝未来函数
- `eval_factor.py` 给出各因子 RankIC/ICIR/t/多空 Sharpe 有效性判定

## 阶段 3：策略层与回测层（2026-07-24）

- 引擎与策略完全解耦（`quant_backtest/` 只认目标权重）；T 信号 → T+1 开盘执行；
  涨跌停/停牌/退市约束；真实费率模型 + 零成本对照
- 首次完整回测（2016–2024，中证500，周频，top50，等权合成）：超额年化 +6.35%、IR 0.67；
  发现换手偏高（后于阶段 5 修正口径统计误差）

## 阶段 4：可视化层（2026-07-24）

- 新增 `quant_web/`（Flask API）+ `frontend/`（React+Vite+ECharts），**零修改**前四层任何文件
- 三页面雏形：回测分析、因子分析、数据概览；回测/因子评估走异步任务 + 进度轮询

## 阶段 5：股票池重构（2026-07-27）

- `quant_factor/universe.py` 单文件 → `quant_factor/universe/` 子包：基础池 BasePool（恒定可投性
  约束）+ 可插拔池（index/size/liquidity/industry/board/custom）+ 组合算子（and/or/then/without）
- 支持字符串简写与 dict spec 组合两种表达；回补沪深300/中证1000/上证50 成分股至 `dim_index_member`
  （此前只有中证500）
- 顺带修正：`engine.py` 换手口径由"双边误计为单边"改为真单边（双边成交额÷2），
  旧回测记录的 16.2x 实为约 8x

## 阶段 6：Web 功能扩充（2026-07-27 ~ 07-28）

- 前端接入完整股票池选择器（可视化构建器 + 高级 JSON 模式），抽成共享 `PoolPicker` 组件，
  回测页/因子页复用
- 新增数据管理控制台（`quant_web/services/pipeline_service.py` + `api/pipeline.py`）：
  日度增量、分阶段回补、离线校验、重建因子面板均可从页面异步触发
- 回测页补全：策略参数（频率/个股权重上限/初始资金）、删除回测记录、持仓/交易 CSV 导出

## 阶段 7：策略注册表（2026-07-28）

- `quant_strategy/base.py` 新增可插拔策略注册表（`StrategyContext`/`StrategySpec`/`REGISTRY`/
  `register_strategy`/`build`），镜像因子层与股票池的注册表模式
- `multifactor` 策略自行注册 `PARAM_SCHEMA`，驱动前端参数表单自动渲染；`backtest_service`
  不再 import 任何具体策略类，只认 `strategy` id + `strategy_params`
- 前端「新建回测」加策略下拉框，参数区完全由 schema 动态渲染；新增策略无需改前端代码

## 阶段 8：全项目安全审查与修复（2026-07-28）

系统性审查全部数据/因子/策略/回测/Web 层代码，对可疑点做真实运行验证（而非仅读代码），发现并修复：

- **SQL 注入 ×2**（Critical）：`data_service.kline`（`GET /api/stocks/<ts_code>/kline`）与
  `MarketData.from_storage`（`POST /api/backtest/runs` 的 `end` 字段）曾把 HTTP 参数直接 f-string
  拼进 DuckDB SQL，可被 `xxx' OR '1'='1` 绕过过滤、读取任意数据甚至本地文件。修复：
  `storage.query()` 支持 `params` 参数化查询，两处改用 `?` 占位符 + 输入校验
- **调仓频率与因子面板静默不匹配**（Critical）：回测页/因子页可选"月频"，但因子面板固定按
  某一频率构建，选错频率时绝大多数调仓日在面板里查不到数据，策略/评估会静默产生几乎不调仓的
  错误结果而不报错。修复：`compute.check_dates_coverage()` 直接校验请求日期在面板的真实命中率，
  命中率过低直接拒绝并提示
- 新增 `tests/test_security.py`（8 项）+ `test_strategy_registry.py` 增补 3 项固定回归；
  全量 pytest 119 passed / 1 xfailed（已知北交所个例）

## 阶段 9：文档整理 + Git 版本管理（2026-07-28）

- 重写 `README.md` 为完整项目文档（架构图、目录结构、各层设计、安全说明、已知限制）
- 新增本 `CHANGELOG.md` 作为阶段性归档记录
- 清理构建产物/缓存（`__pycache__`、`.pytest_cache`、`frontend/dist`），补齐 `.gitignore`
  （排除 `data/`、`info.txt`、`node_modules/` 等）
- 首次 `git init` + 初始提交，本阶段作为版本管理的起点

## 阶段 10：策略实盘跟踪（纸上模拟）（2026-07-29）

- 新增 `quant_web/services/live_service.py`：把回测引擎"拆成按天推进"，组合状态（现金/持仓/
  待执行信号）跨天持久化到 `data/live/{run_id}/`，直接复用回测层撮合/账本/成本模型与策略注册表
- 新增 `scripts/run_live.py`（Task Scheduler 唯一入口：拉当天数据 → 推进全部运行中实例）、
  `quant_web/api/live.py`（创建/列表/详情/停止/删除/手动推进）、前端「策略跟踪」页
  （复用 PoolPicker/策略参数表单组件）
- 只做信号生成 + 虚拟记账，不接触任何真实资金/账户/券商 API
- 用真实历史数据"倒回"验证时发现并修复两处从回测搬到实盘才会暴露的 bug：
  1. **调仓日判断逻辑错误**：截断日历再判断"是否最后一天"会让每天都误判成调仓日，导致
     因子面板被逐日重写、极慢（一次修复顺带发现并清理了因子面板里两个"僵尸日期"——此前在
     数据未拉全时被计算过一次、价量因子全空但从未被增量更新逻辑重新验证过）
  2. **当天全市场数据缺失被误判为集体退市**：回测里"查无行情=真退市"的假设在实盘不成立
     （上游数据当天可能还没出全），会把虚拟组合错误地整个强制清仓
- 新增 `tests/test_live_service.py`（8 项，含 2 项用合成数据固定住上述 bug 的回归）；
  全量 pytest 127 passed / 1 xfailed
