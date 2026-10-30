"""面向 LLM 的工具层（对话式查询 / 战绩点评）。

把 hikari-core 的战绩查询能力暴露成 MaiBot 的 ``@Tool``，让用户可以用自然语言
（"我最近打得怎么样"）而不是指令（``/wws recent``）来查询。

模块分工：

- :mod:`~.extract`    返回数据 -> 给模型看的精简字段（加字段只改这里）
- :mod:`~.query`      查询编排 + 参数拼成 hikari 指令
- :mod:`~.tool_spec`  运行时文案（出错话术、绑定引导、反馈入口）
- :mod:`~.guide`      开关 / 限流 / 错误分类
- :mod:`~.executor`   一次调用的公共流程（查数据 -> 发图 -> 返回文本）

``@Tool`` 装饰的方法本身在 ``plugin.py``（MaiBot 要求组件挂在插件类上）。
导入顺序提醒：``query`` / ``executor`` 依赖 ``hikari_core``，
调用方需先 ``import wows_bot``（它负责把内置源码挂进 ``sys.path``）。
"""

from .executor import ToolContext, ToolExecutor
from .extract import BattleStatsExtractor, normalize_battle_mode
from .guide import (
    ERROR_EXCEPTION,
    ERROR_NETWORK,
    TOOL_SWITCH_FIELDS,
    ToolGuard,
    classify_error,
    config_get_bool,
    humanize_error,
    user_error_text,
)
from .query import (
    COMMAND_BUILDERS,
    QueryResult,
    QueryRunner,
    build_account_command,
    build_bind_command,
    build_literal_command,
    build_recent_command,
    build_roll_command,
    build_ship_command,
    build_ship_recent_command,
    build_ships_command,
    resolve_account_id,
    resolve_identity,
    resolve_ship_name,
)
from .tool_spec import (
    BIND_GUIDE,
    EXCEPTION_GUIDE,
    FEEDBACK_CONTACT,
    IMAGE_FAILED_GUIDE,
    NETWORK_GUIDE,
    SERVER_CODE_HINT,
    USER_EXCEPTION_TEXT,
    USER_NETWORK_TEXT,
)

__all__ = [
    "BIND_GUIDE",
    "BattleStatsExtractor",
    "COMMAND_BUILDERS",
    "ERROR_EXCEPTION",
    "ERROR_NETWORK",
    "EXCEPTION_GUIDE",
    "FEEDBACK_CONTACT",
    "IMAGE_FAILED_GUIDE",
    "NETWORK_GUIDE",
    "QueryResult",
    "QueryRunner",
    "SERVER_CODE_HINT",
    "TOOL_SWITCH_FIELDS",
    "ToolContext",
    "ToolExecutor",
    "ToolGuard",
    "USER_EXCEPTION_TEXT",
    "USER_NETWORK_TEXT",
    "build_account_command",
    "build_bind_command",
    "build_literal_command",
    "build_recent_command",
    "build_roll_command",
    "build_ship_command",
    "build_ship_recent_command",
    "build_ships_command",
    "classify_error",
    "config_get_bool",
    "humanize_error",
    "normalize_battle_mode",
    "resolve_account_id",
    "resolve_identity",
    "resolve_ship_name",
    "user_error_text",
]
