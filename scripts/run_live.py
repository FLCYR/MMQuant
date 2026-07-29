"""策略实盘跟踪（纸上模拟）每日推进入口——手动/临时补跑用。

    python scripts/run_live.py

流程：拉当天最新行情/估值 → 推进全部"运行中"的跟踪实例一天（漏跑自动补齐）。
只做信号生成 + 虚拟记账，不接触任何真实资金/账户/券商 API。

日常自动触发改由 `scripts/scheduler.py`（Python 内置定时任务，替代 Windows
任务计划程序）负责，见 README「Python 定时任务」一节；本脚本仍可用于手动补跑
或调试，逻辑与 scheduler.py 共用 `live_service.daily_cycle()`，行为完全一致。
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

from quant_data.log import get_logger
from quant_web.services import live_service

log = get_logger("run_live")


def main():
    log.info("===== 每日流程：拉取当日数据 + 推进全部实盘跟踪实例 =====")
    result = live_service.daily_cycle()
    log.info("数据更新：%s", result["data_update"].get("message"))

    results = result["advance"]
    if not results:
        log.info("当前没有运行中的实盘跟踪实例")
        return

    for run_id, res in results.items():
        if "error" in res:
            log.error("%s 推进失败：%s", run_id, res["error"])
        else:
            n_days = len(res.get("advanced", []))
            n_trades = res.get("n_trades", 0)
            log.info("%s：推进 %d 个交易日，%d 笔成交", run_id, n_days, n_trades)

    log.info("实盘跟踪推进完成")


if __name__ == "__main__":
    main()
