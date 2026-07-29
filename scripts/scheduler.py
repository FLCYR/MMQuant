"""Python 内置定时任务——替代 Windows 任务计划程序，常驻进程每天固定时间触发一次。

    python scripts/scheduler.py            # 前台常驻，Ctrl+C 退出
    pythonw scripts/scheduler.py            # 后台常驻，无终端窗口（Windows）

启动后长期挂着即可，不需要再配置 Windows 任务计划程序。每天到达 RUN_AT 时刻
（或进程在当天 RUN_AT 之后才启动/重启）触发一次 `live_service.daily_cycle()`
（拉当天行情估值 + 推进全部实盘跟踪实例）；非交易日该流程内部自动判空跳过，
不需要在这里额外判断。触发逻辑幂等，同一天重复触发（如进程重启）无副作用。
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import time
from datetime import datetime

from quant_data.log import get_logger
from quant_web.services import live_service

log = get_logger("scheduler")

RUN_AT = "17:30"          # 每个交易日的触发时间；脚本内部按交易日历自动跳过非交易日
CHECK_INTERVAL = 30       # 轮询间隔（秒）


def run_once():
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
            log.info("%s：推进 %d 个交易日，%d 笔成交", run_id,
                     len(res.get("advanced", [])), res.get("n_trades", 0))


def main():
    log.info("Python 定时任务已启动：每天 %s 触发一次（Ctrl+C 退出）", RUN_AT)
    last_run_date = None
    while True:
        now = datetime.now()
        today = now.strftime("%Y%m%d")
        # >= 而非 ==：进程在 RUN_AT 之后才启动/重启也能补跑当天一次，不用等到次日
        if today != last_run_date and now.strftime("%H:%M") >= RUN_AT:
            last_run_date = today
            try:
                run_once()
            except Exception:
                log.exception("每日流程执行失败")
            log.info("今日已触发，等待下一个交易日 %s", RUN_AT)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("收到退出信号，定时任务已停止")
