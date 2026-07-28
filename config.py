"""数据层全局配置。

token 优先从环境变量 TUSHARE_TOKEN 读取，其次解析 info.txt。
路径、起始日期、指数、限流参数集中在此，便于统一调整。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# ---------------------------------------------------------------- 路径
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"


def _load_token() -> str:
    tok = os.environ.get("TUSHARE_TOKEN")
    if tok:
        return tok.strip()
    info = PROJECT_ROOT / "info.txt"
    if info.exists():
        text = info.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"token\s*=\s*['\"]([0-9a-zA-Z]+)['\"]", text)
        if m:
            return m.group(1)
    raise RuntimeError(
        "未找到 Tushare token：请设置环境变量 TUSHARE_TOKEN，或在 info.txt 中写 token = '...'"
    )


TUSHARE_TOKEN = _load_token()

# ---------------------------------------------------------------- 数据范围
START_DATE = "20150101"          # 历史回补起点
UNIVERSE = "ALL"                 # 全市场 A 股（含退市），消除幸存者偏差

# ---------------------------------------------------------------- 指数与行业
CSI500 = "000905.SH"             # 中证500：默认股票池 / 成分区间表
# 需要落地日线的基准/参考指数
BENCHMARK_INDICES = [
    "000905.SH",   # 中证500
    "000300.SH",   # 沪深300
    "000852.SH",   # 中证1000
    "000001.SH",   # 上证指数
    "399006.SZ",   # 创业板指
]
# 需要回补历史成分区间（供股票池 IndexPool 使用）的广基指数
UNIVERSE_INDICES = [
    "000300.SH",   # 沪深300
    "000905.SH",   # 中证500
    "000852.SH",   # 中证1000
    "000016.SH",   # 上证50
]
SW_INDUSTRY_SRC = "SW2021"       # 申万2021 行业分类

# ---------------------------------------------------------------- 限流
# 每接口每分钟最大调用次数（2000 积分保守值，按实际报错再调）
DEFAULT_RATE_PER_MIN = 300
RATE_LIMITS = {
    "daily": 400,
    "daily_basic": 400,
    "adj_factor": 400,
    "stk_limit": 200,
    "suspend_d": 200,
    "index_daily": 400,
    "index_weight": 200,
    "income": 80,
    "balancesheet": 80,
    "cashflow": 80,
    "fina_indicator": 80,
    "namechange": 200,
    "stock_basic": 100,
    "trade_cal": 100,
    "index_classify": 100,
    "index_member_all": 100,
    "yc_cb": 100,
}

# ---------------------------------------------------------------- 重试 / 分页
HTTP_TIMEOUT = 15                # 单次请求超时（秒）。代理卡住时更快失败并重试
RETRY_BACKOFF = [1, 4, 10, 20]   # 指数退避（秒）
MAX_RETRIES = 4
PAGE_SIZE = 5000                 # 单次返回行数上限（分页步长）

# ---------------------------------------------------------------- 其他
DEFAULT_EXCHANGE = "SSE"         # 交易日历默认以上交所为准
RISK_FREE_DEFAULT_ANNUAL = 2.5   # 无风险利率兜底值（年化%），yc_cb/shibor 都不可用时使用

# ---------------------------------------------------------------- 因子层
FACTOR_DIR = DATA_DIR / "factor"     # 因子面板落地目录
REBAL_FREQ = "W"                     # 调仓/评估频率：W 周频 / M 月频 / D 日频
UNIV_POOL = "csi500"                 # 默认股票池 spec（见 quant_factor/universe）：
# 字符串简写 all/csi500/hs300/csi1000/sz50/index:CODE/size.bottom:K/size.top:K/size.q:LO-HI，
# 或 dict 组合 {and|or|then|without: [...]}，例 {"then":[{"index":"000852.SH"},{"size":{"bottom":100}}]}
UNIV_MIN_LIST_DAYS = 60              # 上市不足 N 个交易日的新股剔除
FACTOR_QUANTILES = 5                 # 分组评估的分位数
WINSOR_MAD = 5.0                     # MAD 去极值倍数
TRADING_DAYS_PER_YEAR = 252          # 年化用

# ---------------------------------------------------------------- 策略/回测层
# 默认因子池：取因子评估中"强/可用"者（LNCAP/ILLIQ 在中证500 内失效，故不入池）
STRATEGY_FACTORS = ["VOL20", "TURN20", "REV20", "EP", "BP", "ROE", "SP"]
STRATEGY_TOPN = 50                   # 持股数
INIT_CASH = 1e7                      # 回测初始资金
