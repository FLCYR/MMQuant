# A 股量化系统 · 数据层开发文档

**版本** v1.0
**范围** 日频、中证500 成分股（后期可更改股票池)、横截面多因子选股策略所需的数据采集、存储与校验
**不含** 分钟/tick 数据、期货期权、另类数据、因子计算与回测逻辑

---

## 1. 设计原则

1. **只存原始数据，不存派生数据。** 复权价、因子值都在使用时计算。存了复权价，一旦有新的除权事件，历史数据全部作废。
2. **一切数据带时点（Point-in-Time）。** 财务数据必须带实际披露日，成分股和行业分类必须带生效区间。这是防未来函数的唯一手段。
3. **写入前校验，校验不过不落库。** 脏数据一旦进入，后面所有因子和回测都是错的，且极难排查。
4. **幂等可重跑。** 任何一天的任务重复执行结果一致，便于失败回补。

---

## 2. 技术选型

| 层 | 选型 | 说明 |
|---|---|---|
| 数据源（主） | Tushare Pro | 数据规范，财务数据带 `ann_date`，复权因子可靠 |
| 存储 | Parquet 文件 + DuckDB | 日频数据量小（几百 MB～几 GB），列存查询快，零运维 |
| 处理 | Python + pandas / polars | — |
| 调度 | cron / APScheduler | 单机足够，不需要 Airflow |

> DuckDB 可直接对 Parquet 文件执行 SQL，无需导入。本文档 DDL 用于定义 schema 语义，实际以 Parquet 分区文件落地即可。

**目录结构建议**

```
data/
├── dim/                          # 维度表，全量覆盖写
│   ├── trade_calendar.parquet
│   ├── stock_basic.parquet
│   ├── index_member.parquet
│   ├── industry_member.parquet
│   └── risk_free_rate.parquet
├── fact/                         # 事实表，按年分区
│   ├── daily_quote/year=2024/*.parquet
│   ├── daily_basic/year=2024/*.parquet
│   ├── financial/year=2024/*.parquet
│   └── index_daily/year=2024/*.parquet
├── meta/
│   ├── sync_log.parquet          # 同步记录
│   └── check_result.parquet      # 校验结果
└── raw/                          # 原始响应留档，便于追溯
```

---

## 3. 数据清单

### 3.1 L0 层 —— 缺失即导致回测错误（最高优先级）

这些数据不产生因子，但缺任何一项，回测结果都不可信。

| 数据 | 用途 | 频率 | Tushare 接口 |
|---|---|---|---|
| 交易日历 | 调仓日对齐、收益计算 | 一次性 + 年度更新 | `trade_cal` |
| 股票基本信息 | 上市/退市日、板块归属 | 每周 | `stock_basic`（含 `list_status=D/P`） |
| 历史成分股 | 消除幸存者偏差 | 每月 | `index_weight` / `index_member_all` |
| 复权因子 | 收益计算 | 每日 | `adj_factor` |
| 停牌记录 | 停牌日不可成交 | 每日 | `suspend_d` |
| ST 标记 | ST 涨跌幅 ±5%，通常需剔除 | 每日 | `namechange` 推导 |
| 涨跌停价 | 涨停买不进 / 跌停卖不出 | 每日 | `stk_limit` |
| 行业分类（历史） | 行业中性化 | 每月 | `index_classify` + `index_member` |
| 基准指数日线 | 超额收益、IR | 每日 | `index_daily` |
| 无风险利率 | 夏普比率 | 每月 | `yc_cb`（国债收益率） |

> **涨跌停价务必取现成字段**，不要自己按 ±10% 推算。主板 10%、创业板/科创板 20%、ST 5%、北交所 30%，且历史上规则有过变更，新股上市首日另有规则。

### 3.2 L1 层 —— 核心因子原料

| 数据 | 覆盖因子 | 频率 | Tushare 接口 |
|---|---|---|---|
| 日线行情 OHLCV | 动量、反转、波动率 | 每日 | `daily` |
| 市值/换手/估值 | 规模、价值、流动性 | 每日 | `daily_basic` |
| 财务指标 | 质量、成长 | 季度 | `fina_indicator` |
| 利润表 / 资产负债表 / 现金流量表 | 自定义财务因子 | 季度 | `income` / `balancesheet` / `cashflow` |

### 3.3 L2 层 —— A 股特色增强（MVP 完成后再加）

融资融券余额（`margin_detail`）、龙虎榜（`top_list`）、大宗交易（`block_trade`）、股东户数（`stk_holdernumber`）、限售解禁（`share_float`）、股东增减持（`stk_holdertrade`）、分红送转（`dividend`）。

> 陆股通/北向持股：2024 年沪深港通调整了披露机制，盘中实时持股已不再披露。历史数据与当前口径在实际接入时需重新确认。

### 3.4 暂不采集

分析师一致预期（免费源质量差）、分钟/tick 数据（日频策略用不上，数据量增加百倍以上）、舆情与另类数据（清洗成本远超收益）。

---

## 4. 表设计

### 4.1 维度表

#### dim_trade_calendar —— 交易日历

```sql
CREATE TABLE dim_trade_calendar (
    cal_date       DATE     NOT NULL,  -- 日期
    exchange       VARCHAR  NOT NULL,  -- SSE / SZSE
    is_open        TINYINT  NOT NULL,  -- 1=交易日 0=非交易日
    pretrade_date  DATE,               -- 上一交易日
    PRIMARY KEY (exchange, cal_date)
);
```

所有日期序列的生成必须以此表为准，禁止使用自然日或 pandas 的 `bdate_range`（不含中国节假日）。

#### dim_stock_basic —— 股票基本信息

```sql
CREATE TABLE dim_stock_basic (
    ts_code      VARCHAR PRIMARY KEY,  -- 600000.SH
    symbol       VARCHAR NOT NULL,
    name         VARCHAR NOT NULL,
    area         VARCHAR,
    market       VARCHAR,              -- 主板/创业板/科创板/北交所
    exchange     VARCHAR,
    list_date    DATE    NOT NULL,
    delist_date  DATE,                 -- 未退市为 NULL
    list_status  VARCHAR NOT NULL,     -- L上市 D退市 P暂停
    update_time  TIMESTAMP
);
```

**必须包含已退市股票**（`list_status='D'`）。只拉在市股票是幸存者偏差最常见的来源。

#### dim_index_member —— 历史成分股（区间表）

```sql
CREATE TABLE dim_index_member (
    index_code  VARCHAR NOT NULL,   -- 000905.SH 中证500
    ts_code     VARCHAR NOT NULL,
    in_date     DATE    NOT NULL,   -- 纳入日
    out_date    DATE,               -- 剔除日，NULL=当前仍在
    PRIMARY KEY (index_code, ts_code, in_date)
);
```

用区间而非快照存储，查询任意历史日期的成分股：

```sql
SELECT ts_code FROM dim_index_member
WHERE index_code = '000905.SH'
  AND in_date <= '2020-06-30'
  AND (out_date IS NULL OR out_date > '2020-06-30');
```

#### dim_industry_member —— 历史行业归属（区间表）

```sql
CREATE TABLE dim_industry_member (
    ts_code        VARCHAR NOT NULL,
    src            VARCHAR NOT NULL,  -- SW2021 / SW2014 / CITIC
    level          VARCHAR NOT NULL,  -- L1 / L2 / L3
    industry_code  VARCHAR NOT NULL,
    industry_name  VARCHAR NOT NULL,
    in_date        DATE    NOT NULL,
    out_date       DATE,
    PRIMARY KEY (ts_code, src, level, in_date)
);
```

公司会变更所属行业，用当前分类回溯历史会导致中性化错误。同一 `(ts_code, src, level)` 在任一时点只能匹配一条记录。

#### dim_risk_free_rate

```sql
CREATE TABLE dim_risk_free_rate (
    trade_date  DATE PRIMARY KEY,
    rate_1y     DOUBLE,   -- 年化，%
    rate_10y    DOUBLE
);
```

### 4.2 事实表

#### fact_daily_quote —— 日线行情（核心表）

```sql
CREATE TABLE fact_daily_quote (
    ts_code       VARCHAR NOT NULL,
    trade_date    DATE    NOT NULL,
    open          DOUBLE,          -- 以下均为不复权原始价
    high          DOUBLE,
    low           DOUBLE,
    close         DOUBLE,
    pre_close     DOUBLE,          -- 前收盘（已考虑除权）
    change        DOUBLE,
    pct_chg       DOUBLE,          -- %
    vol           DOUBLE,          -- 手
    amount        DOUBLE,          -- 千元
    adj_factor    DOUBLE NOT NULL, -- 复权因子
    limit_up      DOUBLE,          -- 当日涨停价
    limit_down    DOUBLE,          -- 当日跌停价
    is_suspended  TINYINT NOT NULL DEFAULT 0,
    is_st         TINYINT NOT NULL DEFAULT 0,
    update_time   TIMESTAMP,
    PRIMARY KEY (ts_code, trade_date)
);
```

**关键设计说明：**

- **存原始价 + 复权因子，不存复权价。** 后复权价 = `close * adj_factor`；前复权价 = `close * adj_factor / adj_factor_latest`。前复权价会随最新除权事件变化，存下来就会失效。
- **收益率计算用复权价，成交价用原始价并与涨跌停比较。** 两者混用是常见错误。
- 可成交性判断在因子/回测层做，但依赖本表字段：

```sql
-- 次日不可买入：涨停或停牌
buyable  = (is_suspended = 0) AND (open < limit_up)
-- 次日不可卖出：跌停或停牌
sellable = (is_suspended = 0) AND (open > limit_down)
```

> `is_st` 由 `namechange` 表推导：股票名称含 "ST" 或 "*ST" 的区间标记为 1。建议单独写一个推导脚本，结果回写本字段。

#### fact_daily_basic —— 每日估值与市值

```sql
CREATE TABLE fact_daily_basic (
    ts_code          VARCHAR NOT NULL,
    trade_date       DATE    NOT NULL,
    turnover_rate    DOUBLE,   -- 换手率 %
    turnover_rate_f  DOUBLE,   -- 自由流通股换手率
    volume_ratio     DOUBLE,
    pe               DOUBLE,   -- 可为负
    pe_ttm           DOUBLE,
    pb               DOUBLE,
    ps_ttm           DOUBLE,
    dv_ttm           DOUBLE,   -- 股息率 %
    total_share      DOUBLE,   -- 万股
    float_share      DOUBLE,
    free_share       DOUBLE,
    total_mv         DOUBLE,   -- 万元
    circ_mv          DOUBLE,
    PRIMARY KEY (ts_code, trade_date)
);
```

市值必须是**日频**的，不能用最新市值回溯历史——市值中性化和规模因子都直接依赖它。

#### fact_financial —— 财务数据（PIT 设计，最易出错）

```sql
CREATE TABLE fact_financial (
    ts_code            VARCHAR NOT NULL,
    end_date           DATE    NOT NULL,  -- 报告期，如 2023-03-31
    ann_date           DATE    NOT NULL,  -- 实际披露日，如 2023-04-28
    report_type        VARCHAR NOT NULL,  -- 1合并报表 2单季合并 ...
    -- 原始科目
    revenue            DOUBLE,
    n_income_attr_p    DOUBLE,   -- 归母净利润
    total_assets       DOUBLE,
    total_equity       DOUBLE,
    n_cashflow_act     DOUBLE,   -- 经营活动现金流净额
    -- 衍生指标
    roe                DOUBLE,
    roa                DOUBLE,
    grossprofit_margin DOUBLE,
    netprofit_margin   DOUBLE,
    debt_to_assets     DOUBLE,
    or_yoy             DOUBLE,   -- 营收同比
    netprofit_yoy      DOUBLE,
    update_flag        TINYINT,  -- 1=修正后数据
    PRIMARY KEY (ts_code, end_date, ann_date, report_type)
);
```

**这张表是未来函数最常见的入口。三条规则：**

1. **主键必须包含 `ann_date`。** 财报会被修正，同一报告期存在多个版本。只保留最新版本 = 用了当时不存在的数据。
2. **查询一律以 `ann_date <= T` 过滤**，绝不能用 `end_date`。2023 年一季报（`end_date=2023-03-31`）实际在 4 月底才披露，3 月 31 日当天你不可能知道。
3. **取每个报告期在 T 时点可见的最新版本。**

标准 PIT 查询模板：

```sql
-- 取 T 日可见的、每只股票最新一期财务数据
WITH visible AS (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY ts_code, end_date
               ORDER BY ann_date DESC
           ) AS ver_rank
    FROM fact_financial
    WHERE ann_date <= :T
      AND report_type = '1'
),
latest_ver AS (
    SELECT * FROM visible WHERE ver_rank = 1
)
SELECT *,
       ROW_NUMBER() OVER (
           PARTITION BY ts_code ORDER BY end_date DESC
       ) AS period_rank
FROM latest_ver
QUALIFY period_rank = 1;
```

建议把这段封装成 `get_financial_pit(date)` 函数，**全项目只允许通过它访问财务数据**，杜绝直接查表。

#### fact_index_daily —— 指数日线

```sql
CREATE TABLE fact_index_daily (
    index_code  VARCHAR NOT NULL,
    trade_date  DATE    NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    pre_close   DOUBLE,
    pct_chg     DOUBLE,
    vol         DOUBLE,
    amount      DOUBLE,
    PRIMARY KEY (index_code, trade_date)
);
```

### 4.3 元数据表

```sql
CREATE TABLE meta_sync_log (
    task_name    VARCHAR NOT NULL,   -- daily_quote / financial ...
    biz_date     DATE    NOT NULL,   -- 业务日期
    status       VARCHAR NOT NULL,   -- SUCCESS / FAILED / PARTIAL
    row_count    INTEGER,
    retry_times  INTEGER,
    error_msg    VARCHAR,
    start_time   TIMESTAMP,
    end_time     TIMESTAMP,
    PRIMARY KEY (task_name, biz_date)
);

CREATE TABLE meta_check_result (
    check_id     VARCHAR NOT NULL,   -- 规则编号，如 C201
    table_name   VARCHAR NOT NULL,
    biz_date     DATE    NOT NULL,
    level        VARCHAR NOT NULL,   -- FATAL / ERROR / WARN
    passed       TINYINT NOT NULL,
    fail_count   INTEGER,
    sample       VARCHAR,            -- 失败样本，便于排查
    check_time   TIMESTAMP
);
```

`meta_sync_log` 是增量更新和失败回补的依据：每次任务启动时查出所有非 SUCCESS 的日期，优先补数。

---

## 5. 数据校验

### 5.1 校验分级与处置

| 级别 | 含义 | 处置 |
|---|---|---|
| FATAL | 数据不可用 | 阻断写入，任务失败，告警 |
| ERROR | 存在明确错误 | 写入隔离区，不进主表，告警 |
| WARN | 可疑但可能正常 | 正常写入，记录待人工复核 |

**校验发生在写入主表之前**，不是之后。流程：拉取 → 落 `raw/` → 校验 → 通过则写主表，不通过则写隔离区并告警。

### 5.2 校验规则清单

#### C1xx 结构层

| 编号 | 规则 | 级别 |
|---|---|---|
| C101 | 字段名与类型符合 schema 定义 | FATAL |
| C102 | 主键无重复 | FATAL |
| C103 | NOT NULL 字段无空值 | FATAL |
| C104 | 单次拉取返回行数 > 0 | FATAL |

#### C2xx 完整性

| 编号 | 规则 | 级别 |
|---|---|---|
| C201 | `fact_daily_quote` 中当日股票数 ≥ 上一交易日的 95% | ERROR |
| C202 | 交易日历中的每个交易日在行情表中都有数据 | ERROR |
| C203 | 每只在市股票（`list_date <= T < delist_date`）当日有行情或有停牌记录 | ERROR |
| C204 | 成分股在其成分区间内的每个交易日都有行情 | ERROR |
| C205 | 财务数据每只股票的报告期序列无跳期（Q1→Q2→Q3→Q4） | WARN |
| C206 | `dim_index_member` 中任一交易日的成分股数量等于指数标称数量（中证500 = 500） | ERROR |

#### C3xx 取值合理性

| 编号 | 规则 | 级别 |
|---|---|---|
| C301 | `open/high/low/close/pre_close > 0` | FATAL |
| C302 | `high >= max(open, close)` 且 `low <= min(open, close)` 且 `high >= low` | FATAL |
| C303 | `vol >= 0`、`amount >= 0` | ERROR |
| C304 | `|pct_chg| <= 21`（考虑创业板 20% + 浮点误差；北交所放宽至 31） | ERROR |
| C305 | `pb > 0`（PB 为负通常是数据错误；PE 可以为负，不校验） | WARN |
| C306 | `0 <= turnover_rate <= 100` | WARN |
| C307 | `total_mv >= circ_mv > 0` | ERROR |
| C308 | `adj_factor > 0` | FATAL |
| C309 | `debt_to_assets` 在 [0, 200] 之外 | WARN |

#### C4xx 一致性（跨字段 / 跨表）

| 编号 | 规则 | 级别 |
|---|---|---|
| C401 | `close` 落在 `[limit_down, limit_up]` 区间内 | ERROR |
| C402 | 本日 `pre_close` 与上一交易日 `close` 的关系符合复权因子变动：`pre_close ≈ prev_close * prev_adj / adj` | ERROR |
| C403 | `adj_factor` 沿时间单调不减（后复权因子只增不减） | ERROR |
| C404 | `total_mv ≈ close * total_share`，相对误差 < 1% | WARN |
| C405 | `ts_code` 在 `dim_stock_basic` 中存在（外键完整性） | ERROR |
| C406 | 停牌日（`is_suspended=1`）成交量应为 0 | WARN |
| C407 | 行情日期在 `[list_date, delist_date]` 区间内 | ERROR |
| C408 | 涨跌停价与板块规则匹配：主板 ±10%、创业板/科创板 ±20%、ST ±5%、北交所 ±30% | WARN |

#### C5xx 时点正确性（PIT，最重要）

| 编号 | 规则 | 级别 |
|---|---|---|
| C501 | `fact_financial.ann_date` 非空 | FATAL |
| C502 | `ann_date >= end_date`（披露日不早于报告期末） | FATAL |
| C503 | `ann_date <= 当前日期`（无未来日期） | FATAL |
| C504 | `ann_date - end_date <= 180 天`，超出为可疑延迟披露 | WARN |
| C505 | `dim_index_member` / `dim_industry_member` 无区间重叠：同一 `ts_code` 在同一 `(index_code)` 或 `(src, level)` 下，任一日期只匹配一条记录 | ERROR |
| C506 | 区间表 `out_date > in_date` 或 `out_date IS NULL` | FATAL |

#### C6xx 异常检测（统计层，跑批后离线执行）

| 编号 | 规则 | 级别 |
|---|---|---|
| C601 | 复权价日收益率绝对值 > 25%（排除已知除权日和北交所）→ 疑似复权错误 | ERROR |
| C602 | 单日全市场收益率均值偏离基准指数收益 > 3 个百分点 | WARN |
| C603 | 某字段当日缺失率较过去 20 日均值上升超过 5 个百分点 | WARN |
| C604 | 财务指标（如 ROE）环比变动超过 10 倍 | WARN |

> **C601 是复权错误的最有效探针。** 复权算错时表现为价格突然跳变，用它扫一遍全历史，能一次性揪出绝大部分复权问题。建议在初次全量拉取完成后立即执行。

### 5.3 校验代码组织

```python
# checks/base.py
@dataclass
class CheckResult:
    check_id: str
    passed: bool
    level: str          # FATAL / ERROR / WARN
    fail_count: int
    sample: str         # 失败样本前 5 条

def run_checks(df, rules) -> list[CheckResult]:
    ...

# checks/rules_quote.py
def c302_ohlc_relation(df) -> CheckResult:
    bad = df[(df.high < df[['open','close']].max(axis=1)) |
             (df.low  > df[['open','close']].min(axis=1)) |
             (df.high < df.low)]
    return CheckResult('C302', bad.empty, 'FATAL', len(bad),
                       bad.head().to_json())
```

规则函数保持无状态、单一职责，一条规则一个函数，便于单独测试和回归。所有结果写入 `meta_check_result`。

---

## 6. 更新流程

### 6.1 日度任务（交易日 17:30 触发）

```
1. 查 dim_trade_calendar：今日是否交易日 → 否则退出
2. 查 meta_sync_log：找出历史失败日期，加入待处理队列
3. 拉取 daily / daily_basic / adj_factor / stk_limit / suspend_d
4. 合并为 fact_daily_quote 与 fact_daily_basic 的待写入分区
5. 执行 C1xx–C4xx 校验
6. FATAL/ERROR → 写隔离区 + 告警 + 标记 FAILED；通过 → 写主表
7. 写 meta_sync_log
8. 执行 C6xx 离线异常检测，输出日报
```

数据源当日数据一般在收盘后 1–2 小时更新完毕，17:30 之后拉取较稳妥。若发现字段缺失，隔 30 分钟重试，最多 3 次。

### 6.2 其他周期

| 任务 | 周期 | 说明 |
|---|---|---|
| 财务数据 | 每周日全量刷近 8 个季度 | 覆盖财报修正与补充披露 |
| 成分股 | 每月 + 指数调整日（6/12 月） | 追加区间记录，不覆盖历史 |
| 行业分类 | 每月 | 同上 |
| 股票基本信息 | 每周 | 关注新增退市股票 |
| 交易日历 | 每年 12 月 | 拉次年日历 |
| 全量校验 | 每月 | 全历史跑一遍 C1xx–C6xx |

### 6.3 限流与容错

Tushare 按积分限制调用频率（不同接口差异较大，低积分账户约每分钟数十至数百次），需要：

- 令牌桶限流器控制调用速率
- 指数退避重试（1s / 4s / 16s，最多 3 次）
- 按股票分批拉取时，单批失败不影响其他批次，失败清单单独记录重试
- 主源连续失败时降级到 AkShare/BaoStock，并在 `meta_sync_log` 标记数据来源

---

## 7. 实施顺序

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| P1 | 交易日历 + 股票基本信息（含退市） | 能取到任意历史日期的在市股票列表 |
| P2 | 日线行情 + 复权因子 + 涨跌停 + 停牌 | C601 复权探针全历史零 ERROR |
| P3 | 校验框架 + `meta_sync_log` + 日度调度 | 连续 5 个交易日自动跑批无人工介入 |
| P4 | daily_basic（市值、估值、换手） | 市值中性化可用 |
| P5 | 成分股 + 行业分类区间表 | 任意历史日期取到的成分股数量正确 |
| P6 | 财务数据 + `get_financial_pit()` 封装 | C5xx 全部通过 |
| P7 | 指数日线 + 无风险利率 | 可计算超额收益与夏普 |

P1–P3 是地基，务必先做扎实。P2 完成后立即跑 C601 全历史扫描——复权问题越早发现越好，它会污染后面所有工作。

---

## 8. 关键风险提示

1. **复权** —— 收益率用复权价，成交价用原始价并与涨跌停比较。混用是最隐蔽的错误之一。
2. **财务数据时点** —— 用 `end_date` 而非 `ann_date` 过滤是最常见的未来函数，且回测结果会好得离谱，容易让人误以为策略有效。
3. **幸存者偏差** —— 退市股票必须保留，成分股必须用历史区间。
4. **区间表重叠** —— 成分股和行业分类的区间若有重叠，join 后行数会膨胀，导致因子值静默出错。C505 必须每次校验。
5. **数据源静默变更** —— 免费源的接口字段和口径可能无通知变更。C6xx 的统计异常检测就是为了捕捉这类问题。
