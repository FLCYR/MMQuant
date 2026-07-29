"""策略实盘跟踪（纸上模拟）每日推进入口——供 Windows 任务计划程序调用。

    python scripts/run_live.py

流程：拉当天最新行情/估值 → 推进全部"运行中"的跟踪实例一天（漏跑自动补齐）。
只做信号生成 + 虚拟记账，不接触任何真实资金/账户/券商 API。
建议交易日 17:30 触发（与 run_daily.py 的既有约定一致）。
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

from quant_data.log import get_logger
from quant_web.services import live_service, pipeline_service

log = get_logger("run_live")


def main():
    log.info("===== 拉取当日最新数据 =====")
    r = pipeline_service.daily_update("cli", {})
    log.info("数据更新：%s", r.get("message"))

    log.info("===== 推进全部实盘跟踪实例 =====")
    results = live_service.advance_all()
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
