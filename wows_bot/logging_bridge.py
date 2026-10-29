"""把内置 hikari-core 的 loguru 日志接进插件的 stdlib logger。

hikari-core 裸用 loguru，默认只写 Runner 进程的 stderr；MaiBot 的日志面板看的是
logging.root 上的 IPC handler，所以这里加一个 loguru sink 把记录转发过去。
必须在导入 hikari_core 之前安装，否则它早期 import 阶段的日志会漏掉。
"""

from __future__ import annotations

import traceback

from .log import logger

_PREFIX = "[hikari-core]"

_installed = False


def _sink(message) -> None:
    try:
        record = message.record
        text = f"{_PREFIX} {record['message']}"
        exc = record["exception"]
        if exc is not None:
            text += "\n" + "".join(
                traceback.format_exception(exc.type, exc.value, exc.traceback)
            )
        logger.log(record["level"].no, text)
    except Exception:
        # 日志桥接自身绝不能影响业务
        pass


def install() -> None:
    """安装 loguru -> stdlib 的转发 sink（幂等）。"""
    global _installed
    if _installed:
        return
    _installed = True

    from loguru import logger as loguru_logger

    loguru_logger.add(
        _sink,
        level="INFO",
        format="{message}",
        enqueue=True,
        backtrace=False,
        diagnose=False,
        catch=True,
    )
