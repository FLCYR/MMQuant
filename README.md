# A 股量化系统

小而精的 A 股日频量化系统：**数据层**（采集→校验→存储，Parquet+DuckDB）→ **因子层**
（13 因子/7 风格，PIT 安全，IC/多空评估）→ **策略层**（可插拔策略注册表，多因子选股为首个实现）
→ **回测层**（T+1 开盘成交、涨跌停/停牌约束、真实费率）→ **Web 控制台**（Flask+React，回测/因子分析/
数据运维全部可视化操作）。数据层详细设计见 [A股量化数据层开发文档.md](A股量化数据层开发文档.md)。

```
quant_data  →  quant_factor  →  quant_strategy  →  quant_backtest
 (采集/校验/存储)   (因子/股票池)      (策略注册表)        (回测引擎)
                                              ↘
                                          quant_web + frontend（可视化控制台，只 import 前四层，零修改）
```

依赖单向：下游只 import 上游，不反向依赖。四个核心包各自可独立测试、独立使用。

## 目录结构

```
config.py                 全局配置（token/路径/起始日期/限流/股票池/策略默认参数）
quant_data/               数据层：采集 → 校验 → 存储
  client.py               Tushare 封装：限流(滑动窗口)+指数退避重试+分页
  storage.py              Parquet 落地 + DuckDB 查询（幂等写、参数化查询防注入）
  calendar.py             交易日历工具
  synclog.py              同步日志（增量与失败回补依据）
  pit.py                  财务 Point-in-Time 访问（唯一入口，杜绝未来函数）
  fetchers/               各数据源采集器（日线/估值/财务/指数/行业/风险利率…）
  checks/                 校验规则 C1xx~C6xx（结构/完整性/取值/一致性/PIT/异常）
quant_factor/             因子层：13 因子 + 股票池
  factors/                因子实现（@register 注册），base.py 存取数辅助
  universe/               股票池：基础池 + 可插拔池（见下文）
  neutralize.py           去极值(MAD) + 行业市值中性化(OLS) + 标准化(zscore)
  evaluate.py             单因子有效性评估（IC/RankIC/ICIR/分组/多空/衰减）
  compute.py              因子面板计算与落地 + 调仓日频率一致性校验
  rebalance.py            调仓日历（周频/月频）
  returns.py              前向收益
quant_strategy/           策略层：可插拔策略注册表
  base.py                 Strategy/Combiner 协议 + StrategyContext + 策略注册表
  strategies/             具体策略实现（当前：multifactor 多因子选股）
  combine.py              多因子合成器（等权 z-score / 滚动 IC 加权）
  construct.py            综合得分 → 目标权重（top-N 等权 / 行业中性）
  constraints.py          权重约束（个股上限等）
quant_backtest/           回测层：引擎与策略完全解耦，只认目标权重
  engine.py               主循环：T 信号 → T+1 开盘执行，逐日盯市
  execution.py            调仓撮合（先卖后买，涨跌停/停牌约束，现金约束）
  market.py               行情容器（依赖注入：生产读库/测试注入合成行情）
  costs.py                真实费率模型（佣金/印花税/过户费/滑点，零成本对照）
  portfolio.py / metrics.py / report.py
quant_web/                Flask API：只 import 上述四层，零修改
  api/                    路由蓝图（回测/因子/数据/任务/数据管道）
  services/                业务逻辑（路由层只做转发）
  jobs.py                 异步任务（线程池 + 进度回报）
  store.py                回测结果落盘/检索
frontend/                 React + Vite + ECharts 控制台
scripts/                  运维脚本（见下表）
tests/                    pytest（数据正确性/纯逻辑/因子/回测/股票池/策略注册表/安全）
data/                     运行时生成，不入 Git：dim/ fact/ factor/ backtest/ meta/ raw/ isolation/ logs/
```

## 快速开始

```bash
# 1. 安装依赖（QUANT 环境已装 tushare/pandas/numpy/requests）
D:\Anaconda\envs\QUANT\python.exe -m pip install -r requirements.txt

# 2. 全历史回补（P1→P7，2-4 小时，可断点续跑）
D:\Anaconda\envs\QUANT\python.exe scripts/backfill.py

# 3. 落地因子面板（多因子策略/因子评估的前置数据）
D:\Anaconda\envs\QUANT\python.exe scripts/build_factors.py

# 4. 日度增量（手动/补跑用；日常自动触发见下方「Python 定时任务」）
D:\Anaconda\envs\QUANT\python.exe scripts/run_daily.py

# 5. 离线全量校验（建议每月）+ 数据现状报告 + 正确性测试
D:\Anaconda\envs\QUANT\python.exe scripts/run_checks.py
D:\Anaconda\envs\QUANT\python.exe scripts/audit.py
D:\Anaconda\envs\QUANT\python.exe -m pytest tests/ -q
```

Tushare token 从环境变量 `TUSHARE_TOKEN` 或 `info.txt` 读取（`info.txt` 含明文 token，**已被
`.gitignore` 排除，切勿提交到版本库**）。

### 脚本一览

| 脚本 | 作用 |
|---|---|
| `backfill.py` | 全历史/分阶段回补（P1~P7），见下表；`--phases`/`--start`/`--end`/`--no-resume` |
| `run_daily.py` | 日度增量：近 10 个交易日内失败自动回补 |
| `run_checks.py` | 离线全量校验：财务 PIT(C5xx) + 区间表(C505/C506/C206) + 复权探针(C601) |
| `audit.py` | 数据现状报告：各表行数/跨度/覆盖率 + 同步日志 + 校验结果 |
| `build_factors.py` | 计算并落地因子面板 `data/factor/{raw,processed}.parquet`；`--start`/`--freq` |
| `eval_factor.py` | 单/全因子有效性报告；`--factor BP\|all` `--start` `--end` `--pool` |
| `run_backtest.py` | CLI 回测；`--combiner equal\|ic` `--industry-neutral` `--topn` `--pool` |
| `run_web.py` | 启动 Flask API（:5000）；`--prod` 用 waitress |
| `run_live.py` | 手动跑一次「日度增量 + 推进全部实盘跟踪实例」，用于补跑/调试 |
| `scheduler.py` | Python 定时任务：常驻进程，每天 17:30 自动跑一次 `run_live.py` 的同一段逻辑 |

### 分阶段回补

```bash
python scripts/backfill.py --phases p1 p2          # 只跑地基与日线
python scripts/backfill.py --start 20200101 --end 20201231
python scripts/backfill.py --phases p6 --no-resume # 财务全量重拉
```

| 阶段 | 内容 | 主要接口 |
|---|---|---|
| P1 | 交易日历 / 股票基本信息(含退市) / 更名 | trade_cal, stock_basic, namechange |
| P2 | 日线行情(+复权+涨跌停+停牌+ST) + C601 | daily, adj_factor, stk_limit |
| P4 | 每日估值市值 | daily_basic |
| P5 | 成分股区间(沪深300/中证500/中证1000/上证50) / 行业区间 | index_weight, index_classify, index_member_all |
| P6 | 财务数据 + PIT | income, balancesheet, cashflow, fina_indicator |
| P7 | 指数日线 / 无风险利率(yc_cb→shibor→常量兜底) | index_daily, yc_cb/shibor |

## 核心设计原则

- **只存原始价 + 复权因子，不存复权价**。复权价使用时算：后复权=`close*adj_factor`。
- **财务数据一律 PIT**：主键含 `ann_date`，查询只走 `pit.get_financial_pit(date)`（按 `ann_date<=T` 取最新版本）。
- **区间表存成分股/行业**，消除幸存者偏差；退市股全保留。
- **写入前校验**：FATAL 违规行入隔离区，其余入主表；ERROR/WARN 记录到 `meta_check_result`。
- **幂等可重跑**：任一天重复执行结果一致，失败按 `meta_sync_log` 自动回补。
- **涉及外部输入的 SQL 一律参数化**：`storage.query(sql, params)` 用 `?` 占位符，禁止把
  HTTP 请求参数直接 f-string 拼进 SQL（历史教训见下文「安全」一节）。

## 测试与数据质量

`tests/`（pytest，120 项）：
- **数据正确性**（`test_data_quality.py`）：结构/主键、行情不变量（OHLC、pct_chg、复权连续 C601）、
  跨表一致（`total_mv≈close×股本`）、PIT 无未来泄漏、覆盖完整、区间表、日历真值。
- **因子正确性**（`test_factor_correctness.py`）：公式验证、PIT 无未来、中性化正交、universe 对齐。
- **股票池**（`test_universe_pools.py`）：基础池不变量、组合算子语义(and/or/then/without)、
  排序池边界、spec 解析。
- **策略注册表**（`test_strategy_registry.py`）：注册枚举、参数合并、协议满足、调仓频率与
  因子面板不匹配防护。
- **回测引擎**（`test_backtest.py`）：合成行情确定性断言（涨跌停/停牌/退市/现金约束）+ 真实数据基准 sanity。
- **安全回归**（`test_security.py`）：SQL 注入拒绝 + 正常请求放行（见下文）。
- **纯逻辑单测**（`test_logic.py`，合成数据）：区间重建、重叠消除、幂等写、PIT 选版。

> 测试严格验证**管道正确性与内部/跨表一致**，无法单独证明上游 Tushare 原始数值绝对准确。
> 已知数据边界：所有复权/覆盖异常集中在**北交所 .BJ**（代码迁移，且不在中证500 池内，检查中已排除）；
> 另有 2 处厂商字段瑕疵（金域医学 603882 的 float>total、300262 增发日市值口径），已在测试中标注容忍。
> **非北交所主板/创业板/科创板数据在所有不变量上干净。**

## 使用数据

```python
import sys; sys.path.insert(0, "D:/QUANT")
from quant_data import storage, calendar, pit

# 某历史日期的成分股 / 在市股票，用交易日历取日期
dates = calendar.get_trade_dates("20200101", "20201231")

# 日线行情（DuckDB SQL 直查 Parquet）
df = storage.read_fact("daily_quote", where="trade_date='20200630'")

# 财务数据必须走 PIT，防未来函数
fin = pit.get_financial_pit("20200630")
```

---

## 因子层 `quant_factor/`

13 个因子 / 7 大风格，全自研 pandas+numpy，周频，中证500 域。

| 风格 | 因子 |
|---|---|
| 价值 | BP, EP, SP |
| 规模 | LNCAP |
| 反转/动量 | REV20, MOM120 |
| 波动 | VOL20 |
| 流动性 | TURN20, ILLIQ(Amihud) |
| 质量 | ROE, GPM |
| 成长 | NPYoY, ORYoY |

处理流程：去极值(MAD) → 行业(SW L1)+市值中性化(OLS 残差) → 标准化(zscore)。
财务因子一律走 `pit.get_financial_pit`（`ann_date<=T`），杜绝未来函数。

### 股票池 `quant_factor/universe/`（基础池 + 可插拔池）

两层分离：**基础池 BasePool**（恒定可投性硬约束：有行情∧非停牌∧非ST∧上市满N日）永远最先执行；
**可插拔池 Pool** 在其输出之上再筛/排序/组合。`pool` 参数是可解析的 spec，三种形态：

- **字符串简写**：`all`、`csi500`/`hs300`/`csi1000`/`sz50`、`index:000905.SH`、
  `size.bottom:200`（市值最小200只）、`size.top:200`、`size.q:0.0-0.3`（市值分位带）、
  `liquidity.top:300`（成交额最高300）、`industry:801780.SI,801150.SI`（SW一级行业子集）、
  `board:创业板`（主板/创业板/科创板/北交所）、`custom:600519.SH,000858.SZ`（静态名单）
- **dict 组合**：`and`(交) / `or`(并) / `then`(串联管道) / `without`(减)，可嵌套。
  例：`{"then": [{"index":"000852.SH"}, {"size":{"bottom":100}}]}` = 中证1000 里市值最小100只；
  流动性可指定口径 `{"liquidity":{"top":300,"by":"turnover"}}`（换手率，默认成交额）
- **Pool 对象**：代码内直接构造

内置池：`all` / `index`(指数成分) / `size`(市值) / `liquidity`(流动性) / `industry`(SW行业) /
`board`(板块) / `custom`(名单)。成员型（index/industry/board/custom）与 `and` 取交、`or` 取并；
排序型（size/liquidity）跟成员池后用 `then` 在成员集合内排序，不能用 `and`（会先全市场排序再取交集）。

所有池 PIT 安全：指数/行业用区间成员表（`in_date<=T<out_date`），市值/流动性用当日横截面，无未来函数。
新增池只需实现 `apply(upstream)->frame` + `@register_pool`，解析与全部调用点零改动。
`universe_frame`/`get_universe` 签名不变，`pool` 默认仍 `'csi500'`。
广基指数成分（300/500/1000/50）由 `scripts/backfill.py --phases p5` 回补至 `dim_index_member`。

```bash
# 单/全因子有效性报告（IC/RankIC/ICIR/分组/多空/衰减 + 相关性矩阵）
D:\Anaconda\envs\QUANT\python.exe scripts/eval_factor.py --factor REV20
D:\Anaconda\envs\QUANT\python.exe scripts/eval_factor.py --factor all --start 20160101

# 落地全历史因子面板（data/factor/raw.parquet, processed.parquet）
D:\Anaconda\envs\QUANT\python.exe scripts/build_factors.py

# 因子正确性测试（公式验证 + PIT无未来 + 中性化正交）
D:\Anaconda\envs\QUANT\python.exe -m pytest tests/test_factor_correctness.py -q
```

**正确性 vs 有效性**：正确性由测试保证（算得对、无前视）；有效性用真实数据的
RankIC/ICIR/t/多空 Sharpe 度量，`eval_factor.py` 给"强/可用/无效"判定，供人工筛选进入多因子合成。

**调仓频率与因子面板一致性**：`data/factor/processed.parquet` 是按固定频率（`config.REBAL_FREQ`，
默认周频）构建的单一文件。任何消费面板的地方（策略构建、因子评估）在使用前都会调用
`compute.check_dates_coverage(dates, panel)` 校验请求的调仓日在面板里的真实命中率，命中率过低
（<90%，通常意味着调仓频率与面板构建频率不一致，例如面板是周频、却选了月频调仓）会直接报错，
而不是静默产生"几乎不调仓"的错误结果。若确实需要不同频率，先用 `build_factors.py --freq M`
按目标频率重建面板。

---

## 策略层 `quant_strategy/`

**可插拔策略注册表**（`quant_strategy/base.py`）：新增策略无需改动引擎、回测服务或前端代码。

```python
# quant_strategy/strategies/xxx.py
PARAM_SCHEMA = [   # 驱动前端表单自动渲染，type: factors/int/float/bool/select
    {"key": "topn", "type": "int", "label": "持股数", "default": 50},
    ...
]

@register_strategy("my_strategy", "策略中文名", "一句话说明", PARAM_SCHEMA)
def _build(params: dict, ctx: StrategyContext) -> Strategy:
    ...  # ctx 提供 dates / pool / get_panel()（懒加载，不用因子面板的策略零成本跳过）/ progress()
    return MyStrategy(...)   # 只需实现 target_weights(T) -> Series
```

在 `strategies/__init__.py` 加一行 `import` 触发注册即可；前端「新建回测」的策略下拉框、参数表单
会自动出现该策略，零改前端代码。当前已注册策略：

| 策略 id | 中文名 | 说明 |
|---|---|---|
| `multifactor` | 多因子选股 | 多因子合成打分（等权 z-score / 滚动 IC 加权），域内取 top-N 等权，可选行业中性 |
| `mars` | MARS 打板回踩 | 大盘放量 + 个股封涨停(反包/突破/主升之一) + 回踩两日不破中位且缩量 → T+3 开盘建仓，破中位或峰值回撤止盈清仓。**事件驱动买入持有**，见下 |

**MARS**（`quant_strategy/strategies/mars.py`）与多因子是**两类不同的策略**：多因子是周期截面
再平衡，MARS 是事件驱动 + 买入持有到止损。它不套用"目标权重再平衡"引擎（那样会被逐日拉回
等权、凭空空转），而是通过注册表的两个可选扩展接入——`cadence='D'`（声明日频）+ 自带
`driver`（复用 Portfolio/CostModel/execution/MarketData/metrics 的小型事件驱动回测循环）。
引擎与其它策略零改动，前端因 schema 驱动自动出现该策略。信号/出场逻辑与口径详见模块文件头。
注意：MARS 为**回测**设计；实盘跟踪页用的是权重再平衡模型，暂不适配 MARS（会逐日再平衡）。
建议扫描域选 `board:主板`（打板标的集中在主板），涨停判定用 `limit_up` 字段自动兼容各板块涨幅。

**多因子选股**的可插拔部件（换任一部件，引擎代码零改动）：
- `Combiner`：`EqualWeightCombiner`（等权 z-score，默认）/ `RollingICCombiner`（滚动 IC 加权，
  严格用 `index < T` 的历史 IC，防未来函数）
- `construct`：top-N 等权 / 行业中性（行业目标权重=域内等权基准占比）
- `constraints`：个股权重上限等后处理

```bash
D:\Anaconda\envs\QUANT\python.exe scripts/run_backtest.py --start 20160101 --end 20241231
D:\Anaconda\envs\QUANT\python.exe scripts/run_backtest.py --combiner ic --industry-neutral --topn 100 --pool csi500
```

---

## 回测层 `quant_backtest/`

纯多头选股、对标中证500。**引擎与策略完全解耦**：引擎只接收 `weights_fn(T) -> Series` 目标权重，
不关心权重怎么来的；`MarketData` 依赖注入（生产读库、测试注入合成行情做确定性断言）。

**回测准确性要点**：
- 信号 T 收盘生成 → **T+1 开盘成交**（结构上无前视）
- **涨停买不进、跌停卖不掉、停牌不可交易、退市强平**（买不进则不买；卖不掉则继续持有）
- 真实费率：佣金万2.5(最低5元)、印花税卖出千1（**2023-08-28 起万5**）、过户费万0.1、滑点万5；
  同时输出**零成本对照**
- 后复权计价（分红送转由复权因子吸收）、停牌按最后价盯市、**逐日盯市**算回撤
- 最小交易金额过滤 + 费用不超过成交额，杜绝碎股与负现金
- 换手口径为**真单边**（双边成交额 ÷ 2 ÷ 调仓前净值），与年化换手标签一致

已知的一处执行细节：买入缩放的成本估算用聚合金额估算最低佣金（非逐笔），当单笔金额远小于约
2 万元时（如持股数很大或资金较小）会轻微低估总成本，可能导致排序靠后的股票被多削减一点；
现金不为负的不变量始终成立（有测试覆盖），只是执行公平性上的一个已知细微偏差。

```bash
# 引擎可靠性测试（合成行情确定性断言 + 真实数据基准 sanity）
D:\Anaconda\envs\QUANT\python.exe -m pytest tests/test_backtest.py -q
```

---

## 策略实盘跟踪（纸上模拟）`quant_web/services/live_service.py`

选一个已注册策略，从"今天"起持续跟踪：每天自动拉最新数据、算目标持仓、与前一日对比生成
买卖信号、虚拟记账。**只做纸上模拟，不接触任何真实资金/账户/券商 API**——生成的是"目标持仓
清单"，要不要真的下单由你自己决定。

设计核心：把回测引擎"拆成按天推进"，组合状态（现金/持仓/待执行信号）跨天持久化到
`data/live/{run_id}/`。T 日收盘算出的目标权重要到 T+1 开盘才执行——在回测里这是同一次调用内
查表完成的，在这里天然对应"今天算的信号，留到明天这次调用里、用明天已经到手的开盘价去执行"。
因此**直接复用**回测层与策略层的既有代码，策略本身不知道自己是在回测还是在实盘里跑：
`quant_backtest.execution.rebalance`（撮合）/`Portfolio`（账本）/`CostModel`（成本）/
`MarketData`（取近30天行情窗口而非全历史）/`quant_strategy.base.StrategyContext`（策略上下文）/
`quant_factor.compute.build`（因子面板增量追加）。

每次推进（`scripts/run_live.py`，每天调用一次）：执行昨天的信号（用今天的开盘价，含涨跌停/
停牌约束）→ 逐日盯市记 NAV → 若今天是该策略频率下的调仓日，算出新目标权重存为待执行信号
（**冻结到下一次推进才执行**，结构上杜绝当天算当天买的未来函数）。创建实例时把创建日视为
"强制调仓日"立即算一次初始信号，不用空等到下个自然周期才建仓。

**两个从回测搬到实盘时才会暴露的坑（已修复，供后续排查参考）**：
- **判断"今天是否调仓日"不能截断日历再看**：`get_rebalance_dates(start, end=今天, freq)` 会把
  日历截断在"今天"，导致"今天"之后同周期的交易日根本没被纳入比较，让任何一天都"看起来"是
  自己周期的最后一天——早期实现犯过这个错，导致每天都被误判成调仓日，因子面板被逐日重写、
  跑得极慢。正确做法是往前多看几天，确认"今天"之后确实没有同周期的交易日了（见
  `live_service._is_rebalance_day`）。
- **当天全市场数据缺失不能当成退市**：回测里"某股票在行情窗口内查无数据"约等于真退市（历史
  数据是终态、完整的），但实盘每天现拉的数据可能当天还没出全（如上游 `daily`/`daily_basic`
  接口比 `adj_factor` 慢一步发布），此时如果直接复用回测的"退市强平"逻辑，会把当天全市场误判
  成集体退市、把整个虚拟组合强制清空。修复：先判断当天是否有任意一只股票的有效收盘价，没有
  就跳过强平和信号执行，原样留到下次推进重试（见 `live_service._advance_one_day` 里的
  `day_has_data` 判断，`tests/test_live_service.py` 已固定回归）。
- 构建策略上下文用的调仓日历史做了**有界截取**（`SIGNAL_LOOKBACK=120` 期，约 2.3 年）而非全部
  历史——否则每次调仓日都要重算 `MultiFactorStrategy` 的全历史 universe/因子分组，随着实盘运行
  时间变长会越跑越慢。120 期对滚动 IC 合成器所需的 26 期历史留了充足冗余。

### Python 定时任务 `scripts/scheduler.py`

不依赖 Windows 任务计划程序：`scripts/scheduler.py` 是一个常驻 Python 进程，内部用
`datetime` 轮询挂钟时间，每天 17:30 自动触发一次`live_service.daily_cycle()`——
拉当天最新行情/估值 → 推进全部"运行中"的跟踪实例（若某天漏跑，下次自动补齐中间缺的
交易日）；非交易日该流程内部自动判空跳过，无需额外判断。触发逻辑幂等，进程重启导致
同一天触发两次也不会有副作用。

```bash
D:\Anaconda\envs\QUANT\python.exe scripts/scheduler.py            # 前台常驻，Ctrl+C 退出
D:\Anaconda\envs\QUANT\pythonw.exe scripts/scheduler.py           # 后台常驻，无终端窗口
```

启动后长期挂着即可（比如开机后手动跑一次，或把第二条命令做成"启动"文件夹里的快捷方式
以便随开机自动运行）。也可以在页面上点"立即推进一天"手动触发，用于测试或补跑；
`scripts/run_live.py` 仍保留作为一次性手动/补跑入口，与 `scheduler.py` 共用同一段
逻辑（`live_service.daily_cycle()`），行为完全一致。

### 云服务器部署（原生部署，无 Docker）

已部署实例：`http://8.134.192.201:8080`（与同机的其它项目共用服务器，走独立端口/
虚拟环境/systemd service，互不干扰）。

- 代码：服务器 `/var/www/mmquant` 是本仓库的 `git clone`（HTTPS，公开仓库无需认证）
- Python：`python3.11 -m venv venv` + `pip install -r requirements.txt`
- 数据：`data/`（gitignore，不进仓库）打包 `tar` 传过去，不在服务器上重新 backfill
- 前端：本地 `npm run build` 打包，`dist/` 传到服务器，nginx 直接挂静态文件
- Token：不写明文 `info.txt`，落在服务器 `/etc/mmquant.env`（`chmod 600`，仅 root
  可读），两个 systemd service 用 `EnvironmentFile=` 引用
- 两个 systemd service：`mmquant-api`（`run_web.py --prod`，waitress，只监听
  127.0.0.1:5000）+ `mmquant-scheduler`（`scheduler.py`，见上）
- nginx：独立 `conf.d/mmquant.conf`，监听 8080，`/` 挂前端静态文件，`/api/` 反代到
  127.0.0.1:5000
- **小内存机器内存兜底**（本机 1.8G 且与其它服务共享）：
  - systemd 给两个 service 设了 `MemoryMax=650M`（cgroup 硬顶）+ `MALLOC_ARENA_MAX=2`，
    单个 service 内存失控只会干净重启、不波及同机其它服务
  - `/etc/mmquant.env` 里设 `DUCKDB_MEMORY_LIMIT=300MB`：DuckDB 默认按系统内存的 80%
    给每查询预算，300MB + Python 基线 ~190MB 才稳稳落在 650M cgroup 内（否则大扫描/
    大回测的单个 DuckDB 查询就会触顶 OOM）。本地开发机内存充裕，用代码默认 512MB 即可，
    不必设此变量
  - MARS 全历史×全主板这类"大扫描域×长区间"回测已按目标行数自适应分批，峰值内存与区间
    长度解耦；实测全历史峰值 ~560MB、连续多次不累积，稳定跑在 650M 内

**以后同步改动**：改完代码在本地跑 `bash scripts/deploy.sh` 即可——推代码到
GitHub、本地打包前端、同步到服务器、服务器拉最新代码、重启两个 service，全自动。

---

## 可视化控制台 `quant_web/`（Flask API） + `frontend/`（React）

**独立新增层**：只 `import` 上述四个包，不修改它们的任何文件；产物写入 `data/backtest/`。

```bash
# 1) 启动 API（终端一）
D:\Anaconda\envs\QUANT\python.exe scripts/run_web.py          # http://127.0.0.1:5000

# 2) 启动前端（终端二）
cd frontend && npm install && npm run dev                     # http://localhost:5173（被占用会自动换端口）
```

四个页面：

- **回测分析**：可选**策略**（下拉框，参数表单按策略的 `PARAM_SCHEMA` 自动渲染）、可视化**股票池
  构建器**（基础指数/板块 + 行业多选 + 市值/流动性叠加筛选，或直接写 JSON 组合 spec，实时预览）、
  频率/初始资金/个股权重上限等参数；超额净值曲线、净值三线（含成本/零成本/基准）、回撤水下图、
  滚动12月超额、分年度收益、累计成本、每期换手、行业暴露（组合 vs 基准）、持仓明细、交易流水；
  支持删除历史回测记录；发起新回测走异步任务（进度条）。
- **因子分析**：同款股票池构建器（评估域应与选股域保持一致）、因子子集选择、有效性总览表
  （RankIC/ICIR/t/多空Sharpe + 强·可用·无效判定）、累计 RankIC 曲线、分组收益、IC 衰减、相关性热力图。
- **策略跟踪**：见上「策略实盘跟踪」——新建跟踪实例（同款策略下拉框+股票池构建器+调仓频率+
  初始资金）、净值曲线（逐日累计，非历史回放）、下一交易日待执行信号预览、当前持仓、交易流水；
  「立即推进一天」手动触发（测试/补跑用）、停止/删除实例。
- **数据概览**：各表行数与跨度、逐年覆盖、校验结果、同步日志；顶部**数据管理控制台**可直接从页面
  触发日度增量、分阶段回补（勾选阶段+区间+断点续补）、离线校验、重建因子面板，均为异步任务、
  带进度与结果回显——不再需要开终端跑脚本。

设计要点：
- 回测/因子评估/数据运维耗时数分钟，一律走**异步任务**（`POST` 拿 `job_id`，前端轮询 `/api/jobs/{id}`）
- 聚合在 DuckDB 侧完成，不把千万级行传给前端；净值支持降采样
- 回测结果落盘 `data/backtest/{run_id}/`，界面默认浏览历史结果，点「新建回测」才重算
- 因子评估结果按参数哈希缓存到 `data/factor/eval/`

### 已知限制（尚未实现，欢迎按需增量补）

- 无多次回测并排对比视图。

个股 K 线页、全局任务中心、交易流水/持仓 CSV 导出均已评估为个人自用场景下的非必要功能，
已整体移除（相关后端接口 `stocks/search`、`stocks/<ts_code>/kline`、`/api/jobs` 列表也一并删除，
仅保留单任务状态查询 `/api/jobs/<job_id>`）。

## 安全

历史上曾在 `个股K线`（`data_service.kline`，功能已整体移除）与 `MarketData.from_storage` 两处
把 HTTP 请求参数（`ts_code`/`start`/`end`）直接用 f-string 拼进 DuckDB SQL，可被 `xxx' OR '1'='1`
之类 payload 注入、绕过过滤条件甚至读取任意本地文件（DuckDB 支持 `read_parquet`/`read_csv` 表
函数）。**已修复**（2026-07-28）：`storage.query()` 扩展为支持 `params` 参数化查询（`?` 占位符），
两处调用点改为参数化 + 输入格式/存在性校验；`MarketData.from_storage` 一侧的回归测试保留在
`tests/test_security.py`。

**约定**：任何拼 SQL 的值只要来自 HTTP 请求参数（而非内部计算出的交易日历/universe 结果），
一律走 `storage.query(sql, params)`，不得用 f-string 直接拼接。

---

## 阶段记录

详见 [CHANGELOG.md](CHANGELOG.md)。
