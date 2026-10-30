"""LLM 工具的开关、限流与话术处理。

三件事：

1. **开关**：总开关 + 每个工具的独立开关（配置见 ``_conf_schema.json``）。
2. **限流**：LLM 可能被引导反复调用工具（刷屏 / 打爆上游接口），按会话限制频率。
3. **话术**：把 hikari 的机读错误（如「请先绑定」）换成给模型的**行动指引**
   （比如引导用户去绑定），而不是让模型把原始报错念给用户。
   故障还分了「网络波动（提示重试）」与「程序异常（引导加群反馈）」两类，
   见 :func:`classify_error`。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from ..wows_bot.log import logger

from .tool_spec import (
    BIND_GUIDE,
    EXCEPTION_GUIDE,
    NETWORK_GUIDE,
    UNBOUND_MARKERS,
    USER_EXCEPTION_TEXT,
    USER_NETWORK_TEXT,
)

__all__ = [
    "ERROR_EXCEPTION",
    "ERROR_NETWORK",
    "ToolGuard",
    "classify_error",
    "humanize_error",
    "user_error_text",
]


# ===========================================================================
# 配置读取
# ===========================================================================

# 工具名 -> 配置里的开关字段（均位于 `[llm_tool]` 分组下）。
# 加工具时在这里登记，默认值统一为 True。
TOOL_SWITCH_FIELDS: dict[str, str] = {
    "wws_account": "enable_account",
    "wws_ship": "enable_ship",
    "wws_recent": "enable_recent",
    "wws_recent_random": "enable_recent_random",
    "wws_recent_rank": "enable_recent_rank",
    "wws_ship_recent": "enable_ship_recent",
    "wws_recent_battles": "enable_recent_battles",
    "wws_ships": "enable_ships",
    "wws_roll_ship": "enable_roll_ship",
    "wws_bind": "enable_bind",
    "wws_bind_list": "enable_bind_list",
    "wws_bind_change": "enable_bind_change",
}


def config_get(config, key: str, default=None):
    """从配置对象里取值，兼容对象属性与普通映射两种形态。"""
    if config is None:
        return default
    try:
        getter = getattr(config, "get", None)
        if callable(getter):
            value = getter(key, default)
            return default if value is None else value
        value = getattr(config, key, None)
        return default if value is None else value
    except Exception:
        return default


def config_get_bool(config, key: str, default: bool = True) -> bool:
    """取布尔配置，容忍 "false" / "0" 这类字符串写法。"""
    value = config_get(config, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    if value is None:
        return default
    return bool(value)


def config_get_int(config, key: str, default: int) -> int:
    """取整数配置，非法值回退默认。"""
    value = config_get(config, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ===========================================================================
# 话术
# ===========================================================================

# hikari 的机读错误 -> 给模型的行动指引。
# 命中后模型应该照指引去做（比如引导用户绑定），而不是把原文念给用户。
_ERROR_GUIDES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        UNBOUND_MARKERS,
        BIND_GUIDE,
    ),
    (
        ("服务器名", "服务器输入错误"),
        "服务器名无法识别。请告诉用户可用的服务器名（亚服 / 国服 / 欧服 / 美服 / 俄服），"
        "确认后重新调用工具；如果用户其实是要查自己，改为不传 server 和 nickname。",
    ),
    (
        ("accountId不允许为空", "accountId长度不符合规范"),
        "查别人时必须同时给出服务器和昵称，只给昵称定位不到账号。"
        "请向用户确认服务器（亚服 / 国服 / 欧服 / 美服 / 俄服），"
        "带上 server 和 nickname 重新调用本工具；如果用户其实是要查自己，"
        "改为不传 server 和 nickname，或先让用户绑定账号。",
    ),
    (
        ("用户名不符合规范",),
        "这个昵称在该服务器下无效：多半是服务器选错，或把 QQ 昵称 / 群名片当成了游戏昵称。"
        "请向用户确认游戏内昵称与所在服务器后重试；"
        "如果用户其实已经绑定过账号（可先用 wws_bind_list 确认），不要再绑定，"
        "直接查自己（调用 wws_account / wws_recent，不传 server 和 nickname）。",
    ),
    (
        ("用户不存在", "账号不存在", "未找到该用户", "未找到对应"),
        "没有找到这个昵称对应的游戏账号。请向用户确认服务器与昵称是否与游戏内完全一致"
        "（不要带公会标签、不要多空格；昵称含空格时整体加括号），确认后重新调用本工具；"
        "如果用户其实想查自己，可以先调用 wws_bind_list 看已绑定的账号；"
        "已经绑定过的话不要再绑定，直接查自己（不传 server 和 nickname）。",
    ),
    (
        ("未识别", "请发送wws help", "无法识别参数"),
        "指令参数无法识别。请检查你拼的参数是否符合工具说明，"
        "或者询问用户更明确的信息后重试。",
    ),
    (
        ("该战舰数据不存在", "查询不到战绩数据"),
        "这艘船在这个账号下没有战绩数据：可能没买过、没开过，"
        "也可能是上游缺少这艘船的数据（超战等特殊船常见）。"
        "请如实告诉用户，并建议确认船名与服务器；"
        "不要把整账号总表当成这艘船的结果发出去。",
    ),
    (
        ("找不到船",),
        "船名没有匹配到任何战舰。请确认用户说的是完整船名（可以给中文全名或英文名），"
        "不要让用户继续用简称或外号猜；确认后重新调用本工具。",
    ),
    (
        ("没有随机战数据记录", "没有排位战数据记录", "数据记录不存在"),
        "该时间范围内没有对应模式的战斗记录。请如实告诉用户这段时间没有打，"
        "可以建议换一个天数范围（例如从 7 天改成 30 天）再查。",
    ),
    (
        ("无recent记录", "无 recent 记录", "无近期记录"),
        "这个时间段内没有查到战斗记录（不是故障）。请如实告诉用户这段时间没有打，"
        "并建议放宽范围（例如「今天」→ 30 天）再查；单船查询还可以顺带确认船名是否写对。",
    ),
    (
        ("仅管理员", "该指令仅管理员可用"),
        "这个功能仅机器人管理员可用，请礼貌地告诉用户无法执行。",
    ),
)


def humanize_error(message: str, kind: str = "") -> str:
    """把机读错误换成给模型的行动指引；识别不了就原样返回。

    先按 ``kind`` 分流出「程序异常 / 网络波动」这两类**与业务无关**的故障
    （见 :func:`classify_error`），再走业务错误的话术表。

    刻意保留原文：模型拿到模糊错误时仍能如实转述，不会因为这里没登记就丢信息。
    """
    if kind == ERROR_EXCEPTION:
        return EXCEPTION_GUIDE
    if kind == ERROR_NETWORK:
        return NETWORK_GUIDE
    if not message:
        return "查询没有返回内容，请稍后重试。"
    text = str(message)
    for markers, guide in _ERROR_GUIDES:
        if any(marker in text for marker in markers):
            return f"{guide}\n（原始提示：{text}）"
    return text


# ===========================================================================
# 故障分类
#
# 为什么要把「网络波动」和「程序异常」分开：
#   · 网络问题让用户重试就够了，是预期内的；
#   · 程序异常（我们自己的 bug）用户重试多少次都没用，必须让作者知道 ——
#     这类如果笼统回一句「请稍后重试」，用户会一直白试，问题也永远暴露不出来。
# ===========================================================================

ERROR_NETWORK = "network"
ERROR_EXCEPTION = "exception"

# 网络类异常的类名（第三方库各写各的，按名字与模块名兜底判定）
_NETWORK_ERROR_CLASSES = frozenset({
    "TimeoutError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ConnectError", "ReadError", "WriteError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError", "ConnectionAbortedError",
    "ConnectionRefusedError", "NetworkError", "NetworkUnreachable",
    "SSLError", "SSLZeroReturnError", "ProxyError", "ClosedResourceError",
})

# 异常信息来源与超时的第三方模块（openai / httpx / httpcore 等）
_NETWORK_ERROR_MODULES = ("httpx", "httpcore", "http.client", "aiohttp", "openai", "urllib3", "requests")

# 消息里出现这些词也按网络类处理
_NETWORK_KEYWORDS = (
    "timeout", "timed out", "超时",
    "connection", "连接",
    "temporary failure in name resolution", "name or service not known",
    "network", "网络",
    "ssl", "proxy",
    "502", "503", "504",
    "server disconnected", "remotedisconnected",
    "远程主机强迫关闭", "对方主机", "10054", "10060",
    "max retries exceeded",
)


def classify_error(exc: BaseException) -> str:
    """把异常归类成 :data:`ERROR_NETWORK` 或 :data:`ERROR_EXCEPTION`。"""
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        # OSError 覆盖了各种 socket 层错误（含 WinError 10054/10060）
        return ERROR_NETWORK

    # 上游 5xx：是服务端暂时不可用，让用户重试；4xx 多半是请求本身有问题，按其他错误走
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and status >= 500:
        return ERROR_NETWORK

    module = (type(exc).__module__ or "").lower()
    name = type(exc).__name__
    if name in _NETWORK_ERROR_CLASSES:
        return ERROR_NETWORK

    # 消息里的网络特征优先判定：像 httpx.HTTPStatusError 这类名字里不带
    # timeout/connect 的，只能靠正文里的 "503" / "connection" 之类识别
    text = str(exc).lower()
    if any(keyword in text for keyword in _NETWORK_KEYWORDS):
        return ERROR_NETWORK

    if any(module.startswith(m) for m in _NETWORK_ERROR_MODULES):
        # 传输层库抛出的其余异常：名字里有网络特征才算网络类，否则是我们自己用错了
        if any(word in name.lower() for word in ("timeout", "connect", "network", "ssl", "proxy")):
            return ERROR_NETWORK
        return ERROR_EXCEPTION
    return ERROR_EXCEPTION


def user_error_text(kind: str) -> str:
    """给用户的兜底文案（模型没照做时由插件直接说）。"""
    if kind == ERROR_EXCEPTION:
        return USER_EXCEPTION_TEXT
    return USER_NETWORK_TEXT


# ===========================================================================
# 限流
# ===========================================================================


class ToolGuard:
    """按会话限制工具调用频率。"""

    def __init__(self, config, window_seconds: float = 60.0):
        self._config = config
        self._window = window_seconds
        # 会话 -> 最近的调用时间戳（只留窗口内的，天然不增长）
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    @property
    def limit(self) -> int:
        """每个窗口内允许的调用次数；<=0 表示不限流。"""
        return config_get_int(self._config, "rate_limit", 10)

    def enabled(self) -> bool:
        """LLM 工具总开关（实验性，缺省关闭）。"""
        return config_get_bool(self._config, "enabled", False)

    def tool_enabled(self, tool_name: str) -> bool:
        """单个工具的开关；未登记的工具体默认放行。"""
        field = TOOL_SWITCH_FIELDS.get(tool_name)
        if not field:
            return True
        return config_get_bool(self._config, field, True)

    def check(self, session: str) -> str | None:
        """记录一次调用；超限时返回给用户的提示，未超限返回 None。"""
        limit = self.limit
        if limit <= 0:
            return None
        now = time.monotonic()
        bucket = self._calls[session]
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        if len(bucket) >= limit:
            wait = max(1, int(self._window - (now - bucket[0])))
            logger.info(f"LLM 工具调用超限 session={session} limit={limit}/{self._window:.0f}s")
            return f"查询太频繁啦，请等 {wait} 秒后再试~"
        bucket.append(now)
        return None

    def reset(self) -> None:
        """清空限流记录（插件卸载时用）。"""
        self._calls.clear()
