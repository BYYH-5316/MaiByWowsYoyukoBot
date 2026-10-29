"""本插件专用的 stdlib logger。

Runner 进程会把 logging.root 上的记录转发给主进程，所以名字只要稳定可识别即可；
`plugin.` 前缀便于在 MaiBot 日志里筛出插件输出。
"""

import logging

LOGGER_NAME = "plugin.community.wows-yuyuko"

logger = logging.getLogger(LOGGER_NAME)
