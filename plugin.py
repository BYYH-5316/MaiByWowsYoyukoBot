"""战舰世界战绩查询插件（MaiBot 接入层 / 唯一入口）。

- 上游原版：AstrBot 插件 AstrByWowsYuyukoBot，作者 西行寺雨季
  https://github.com/JustOneSummer/AstrByWowsYuyukoBot
- 渲染核心：hikari-core v2.0.1，作者 Hikari / benx1n（MIT）
  https://github.com/benx1n/HikariBot
- 战绩数据：yuyuko API https://v3-api.wows.shinoaki.com
- MaiBot 移植与维护：白云鱼海

指令：

- `/wws <参数>`      查询并渲染水表图片（帮助、绑定、单船、近期等全部子指令）
- 纯数字回复          消费 `/wws` 交互流程里「等用户回复序号」的那一步

群聊 @（不需要 @ 机器人）：

- `@某位群友 查水表` 直接查那位群友绑定账号的总表并发图；触发词可在配置里改

LLM 工具（`@Tool`，见文件后半段）：

- 12 个 `wws_*` 工具，让用户在聊天里用自然语言查询战绩，AI 拿精简数据作答/点评，
  图片仍按 `/wws` 的排版渲染发给用户。

渲染与数据都在 `hikari_core`（随插件分发）；本文件只做组件适配与生命周期管理。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, NamedTuple

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

from maibot_sdk import Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder, ToolParameterInfo, ToolParamType

# 先导入 wows_bot：它的 __init__ 会把插件根目录挂进 sys.path，
# 之后才能把随包分发的 hikari_core 当顶级包导入。
from .config import WowsSettings
from .wows_bot import (
    CoreRuntime,
    OutputSender,
    SelectionManager,
    account_not_found_hint,
    mention_header,
    missing_token_hint,
    reply_text,
)
from .wows_bot.log import logger
from .yuyuko_llm import (
    COMMAND_BUILDERS,
    ERROR_EXCEPTION,
    ToolContext,
    ToolExecutor,
    ToolGuard,
    build_account_command,
    build_bind_command,
    build_literal_command,
    build_recent_command,
    build_roll_command,
    build_ship_command,
    build_ship_recent_command,
    build_ships_command,
    classify_error,
    resolve_account_id,
    resolve_identity,
    resolve_ship_name,
    ship_name_exists,
    user_error_text,
)
from hikari_core import callback_hikari, init_hikari

# yuyuko API 的 platformType（与聊天平台无关，固定 QQ）
PLATFORM = "QQ"

# 近期查询的天数上限（防止模型填 99999 把上游拖死）
# yuyuko 的 recent 接口硬上限是一年，实测 366 天可用、367 天回「day:最大查询时间一年」
MAX_RECENT_DAYS = 366

def _identity(server: str | None, nickname: str | None) -> tuple[str, str]:
    """统一「查自己 / 查别人」的口径：只给一个也算给了，交给拼装层处理。"""
    return (server or "").strip(), (nickname or "").strip()


def _clamp_days(days: int | None) -> int | None:
    """把天数夹到合理区间；非正数当作没给。"""
    try:
        value = int(days) if days is not None else 0
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return min(value, MAX_RECENT_DAYS)


# 中文时间说法 -> 天数；数字写法（90 天 / 两个月）单独解析
_TIME_WORD_DAYS = (
    (("近一年", "一年", "12个月", "十二个月"), 360),
    (("半年", "六个月", "近半年"), 180),
    (("三个月",), 90),
    (("两个月", "近两个月", "这俩月"), 60),
    (("这个月", "这一个月", "本月", "一个月", "近一个月"), 30),
    (("这周", "本周", "一周", "近一周", "这礼拜"), 7),
    (("这几天", "这三天", "三天"), 3),
    (("昨天", "昨日"), 2),
    (("今天", "今日", "当天"), 1),
)

# 中文数字也要认：「八个月」「十二天」以前只按阿拉伯数字剥，会被当成船名
_CN_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
# 月份必须带「个」：不带的「九月七日」是船名（用户实测有这条船），当日期或时长都会丢船
_CN_TIME_RE = re.compile(
    r"(?:近|最近)?\s*(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(个\s*月|天|周)"
)

# 「西西里recent36」「西西里近期95」：带 recent / 近期 的裸数字就是天数。
# 后面跟着 场/把/局/艘 时不算——那是「最近 30 把」这种局数，不是天数。
_RECENT_NUM_RE = re.compile(
    r"(?<![A-Za-z])(?:recent|近期|最近)\s*(\d{1,3})(?!\s*(?:[A-Za-z0-9]|场|把|局|次|艘|条|只|个))",
    re.IGNORECASE,
)

# 「西西里recent」「西西里近期」：cue 后面没跟数字就是今天（1 天）。
# 必须排除「最近30把」这类局数（cue 后面紧跟数字），否则会把 30 把当成 1 天。
_BARE_RECENT_RE = re.compile(
    r"(?<![A-Za-z])(?:recent|近期|最近)(?![A-Za-z])(?![^\S\r\n]*\d)", re.IGNORECASE
)

# 「30近期」「190recent」：数字写在 cue 前面的写法（用户实测 `/30近期` 指最近 30 天）
_RECENT_NUM_PREFIX_RE = re.compile(
    r"(?<![\w-])(\d{1,3})\s*(?:recent|近期|最近)(?![A-Za-z])", re.IGNORECASE
)

# 「半个月」「一个半月」「两个半月」：半月是固定说法，前面可以不带数字。
# 一个半月 = 45 天（用户口径：一个月 30 + 半个月 15）
_CN_HALF_MONTH_RE = re.compile(
    r"(?:(?P<n>\d{1,2}|[一二两三四五六七八九十]{1,3})\s*个\s*)?半\s*个?\s*月"
)

# 「7日水表」的「日」= 天。只认阿拉伯数字：中文数字的「九月七日」是一条船，
# 把它当 7 天会把船名剥成「九月」（用户实测这条船在）。
_NUM_DAY_RE = re.compile(r"(?<![A-Za-z\d-])(\d{1,3})\s*日")

# 「近期无比战绩30」「最近 大和 30」：天数写在船名后面，没紧贴 recent/近期。
# 只在句子里确实有 recent/近期/最近 时才把独立数字当天数；
# 「最近30把」「连输5场」这类局数由后面的负向断言排除。
_CUE_ANY_DAYS_RE = re.compile(
    r"(?<![A-Za-z\d-])(\d{1,3})(?!\s*(?:[A-Za-z\d]|场|把|局|次|艘|条|只|个))"
)

# 「单独一条 /<查询>」的写法：整条消息去掉 @ 标记后必须正好是 ``/xxx``
_SIMPLE_PREFIX_RE = re.compile(r"^[/／]\s*(?P<payload>\S(?:[^\n]*\S)?)$")
# 正常指令的开头词：``/wws me recent``、``/ship recent …`` 这类一律不抢。
# 配置里的指令词（默认 wws）另外判断，这里只兜住 hikari 自己的子命令。
_SIMPLE_BLOCK_HEADS = {
    "wws", "me", "ship", "career", "rank", "random", "clan", "auth", "bind",
    "help", "update", "version", "recent_rank",
}
# 别的机器人（官方水表 bot：战舰世界yuyuko助手 / 战舰世界-kokomi…）被 @ 时整条让开：
# 群里那条消息本来就是在跟官方 bot 说话，我们插一脚会莫名其妙多出一张水表。
# 判定用 @ 列表里的显示名，机器人自己的名字池（宿主配置 + 群里学到的名片）另外排除。
_FOREIGN_BOT_NAME_RE = re.compile(r"yuyuko|kokomi|助手|bot|robot|机器人", re.IGNORECASE)
# 船名里出现连词就说明这条消息点了不止一艘船，简易查询不接（交回正常路径）
_SHIP_CONJUNCTION_RE = re.compile(r"[和与及或、,，/]")


def _cn_to_int(value: str) -> int | None:
    """把「八」「十二」「二十」转成整数；认不出来返回 None。"""
    if value.isdigit():
        return int(value)
    if not value:
        return None
    if "十" not in value:
        return _CN_DIGITS.get(value) if len(value) == 1 else None
    tens, _, ones = value.partition("十")
    if len(tens) > 1 or len(ones) > 1:
        return None
    tens_value = _CN_DIGITS.get(tens, 1) if tens else 1
    if tens and tens not in _CN_DIGITS:
        return None
    if ones and ones not in _CN_DIGITS:
        return None
    return tens_value * 10 + (_CN_DIGITS.get(ones, 0) if ones else 0)


def _days_in_text(text: str) -> int | None:
    """从用户原话里抠天数；只是为了兜住模型漏填的 days，抠不出就返回 None。"""
    if not text:
        return None
    clean = _strip_at_tokens(text)
    # 半个月 / 一个半月 先认：数字部分要么没有、要么被「半」隔开，_CN_TIME_RE 抓不到
    half = _CN_HALF_MONTH_RE.search(clean)
    if half:
        number = _cn_to_int(half.group("n")) if half.group("n") else 0
        if number is not None:
            return _clamp_days(number * 30 + 15)
    day = _NUM_DAY_RE.search(clean)
    if day:
        return _clamp_days(int(day.group(1)))
    match = _CN_TIME_RE.search(clean)
    if match:
        number = _cn_to_int(match.group(1))
        if number:
            unit = match.group(2)
            if unit.endswith("周"):
                # 用户口径：一周 7 天，两周按半个月算 15 天
                return _clamp_days(15 if number == 2 else number * 7)
            factor = 30 if unit.endswith("月") else 1
            return _clamp_days(number * factor)
    match = _RECENT_NUM_RE.search(clean)
    if match:
        number = _cn_to_int(match.group(1))
        if number:
            return _clamp_days(number)
    match = _RECENT_NUM_PREFIX_RE.search(clean)
    if match:
        number = _cn_to_int(match.group(1))
        if number:
            return _clamp_days(number)
    for words, days in _TIME_WORD_DAYS:
        if any(word in clean for word in words):
            return days
    # 「西西里recent」「西西里近期」：只有 cue、没写数字，就是今天（1 天）。
    # 以前它被当噪音词剥掉，船名留下、天数是 None，整条消息退化成生涯总表。
    # 夹着聊天词（「最近手感不好」）就不认，免得把闲聊当时间。
    if _BARE_RECENT_RE.search(clean) and not _QUERY_NOISE_RE.search(clean):
        # 「近期无比战绩30」：数字被船名隔开、没紧贴 cue，也算天数
        match = _CUE_ANY_DAYS_RE.search(clean)
        if match:
            number = _cn_to_int(match.group(1))
            if number:
                return _clamp_days(number)
        return 1
    return None


def _ship_in_text(text: str) -> str:
    """从 @ 消息里抠船名候选：剥掉闲聊词、时间词和标点，剩下的当船名。

    只是给 @ 群友这条确定性路径用。剥不干净（例如「他的」只剥到一半）时，
    上游会回「找不到船」，调用方再退回总表/近期，不会卡死。
    """
    if not text:
        return ""
    value = _strip_at_tokens(text)
    # 「半个月」「一个半月」先剥：_CN_TIME_RE 的数字部分抓不到「半」
    value = _CN_HALF_MONTH_RE.sub(" ", value)
    # 「7日水表」：日 = 天，不剥的话「7日」会被当成船名
    value = _NUM_DAY_RE.sub(" ", value)
    # 「近7天」「最近90天」「八个月」先整段剥。必须赶在 _RECENT_NUM_RE 前：
    # 「最近90天」若先按裸数字剥掉「最近90」，剩下的「天」会粘到船名上（天4501）
    value = _CN_TIME_RE.sub(" ", value)
    # 「4501recent180」「4501近期95」整段剥，必须赶在剥「近期 / 最近」之前，
    # 否则词没了只剩数字，船名就成了「4501 95」
    value = _RECENT_NUM_RE.sub(" ", value)
    for word in _MENTION_NOISE_WORDS:
        value = value.replace(word, " ")
    for word in _TIME_WORDS:
        value = value.replace(word, " ")
    # 只剥明确的英文参数词（recent / single …）。不能整段删掉英文字母：
    # 「AL信浓」「ARP大和」「HSF哈巴」这些联动船的前缀本身就是船名的一部分。
    value = _EN_NOISE_RE.sub(" ", value)
    value = value.replace("的", " ")
    # 「西西里recent36」剥完剩一串数字，别让它混进船名；
    # 但只剩数字时不能剥：4501 就是 U-4501（上游按名称模糊匹配），
    # 剥掉会让船名变成空、整条消息退化成查整账号。
    # 带字母或连字符的数字（U-190 / I-56 / S-189 / u4501）本来就保留
    if re.search(r"[^\d\s-]", value):
        value = re.sub(r"(?<![\w-])\d{1,4}(?![\w-])", " ", value)
    value = re.sub(r"[^\w-]+", " ", value, flags=re.UNICODE)
    # 「AL 信浓」这种带空格的写法会被上游当成「服务器 昵称」参数，拼回去
    value = re.sub(r"(?<=[A-Za-z])\s+(?=[\u4e00-\u9fff])", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -")
    # 纯数字只有 3 位以上才当船名（4501 → U-4501）；「36」这种更像天数，别当船
    if not value or len(value) > 24 or (value.isdigit() and len(value) < 3):
        return ""
    return value


def _query_residue_empty(text: str) -> bool:
    """去掉时间说法、查询词、船名之后是否什么都不剩。

    用来判断「整条消息就是一次查询」：``/recent``、``今天``、``西西里recent30``
    剥完是空的；``我今天打了一局``、``最近手感不好`` 剥完还剩内容，不算。
    """
    value = _strip_at_tokens(text or "").strip()
    value = value.lstrip("/／!！ ").strip()
    if not value:
        return False
    ship = _ship_in_text(value)
    rest = _CN_HALF_MONTH_RE.sub(" ", value)
    rest = _NUM_DAY_RE.sub(" ", rest)
    rest = _CN_TIME_RE.sub(" ", rest)
    rest = _RECENT_NUM_RE.sub(" ", rest)
    rest = _BARE_RECENT_RE.sub(" ", rest)
    for word in (*_MENTION_NOISE_WORDS, *_TIME_WORDS):
        rest = rest.replace(word, " ")
    rest = _EN_NOISE_RE.sub(" ", rest)
    rest = rest.replace("的", " ")
    if ship:
        rest = rest.replace(ship, " ")
    rest = re.sub(r"(?<![\w-])\d{1,4}(?![\w-])", " ", rest)
    rest = re.sub(r"[\s\W_]+", "", rest, flags=re.UNICODE)
    return not rest


def _looks_like_query(text: str) -> bool:
    """这条消息是不是在查战绩（用于 AI 工具的消息校验）。"""
    clean = _strip_at_tokens(text or "")
    if not clean:
        return False
    # 先按「单独一条 /查询」整段判断：`/recent 怎么用` 这种虽然带 recent，也是在问用法
    payload = _simple_query_payload(text)
    if payload:
        return _simple_payload_kind(payload, _ship_in_text(payload)) is not None
    if _QUERY_HINT_RE.search(clean):
        return True
    # 「今天」「近期」「西西里recent」：不带 / 也算，但不能夹带聊天词
    if _QUERY_NOISE_RE.search(clean):
        return False
    return _days_in_text(clean) is not None and _query_residue_empty(clean)


def _simple_payload_ok(payload: str) -> bool:
    """整条 ``/`` 片段是不是「纯查询」：无聊天词、长度正常、剥完不剩东西。"""
    if not payload or len(payload) > _SIMPLE_PAYLOAD_MAX:
        return False
    if _QUERY_NOISE_RE.search(payload):
        return False
    return _query_residue_empty(payload)


def _query_cue_only(payload: str) -> bool:
    """整段只有「水表 / 查水表 / 出战绩」这类查询词（可带动词），没有船名也没有时间。"""
    value = payload
    for word in (*_MENTION_NOISE_WORDS, *_DEFAULT_MENTION_KEYWORDS):
        value = value.replace(word, " ")
    value = re.sub(r"[查看来整发出求]", " ", value)
    value = re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)
    return not value


def _simple_payload_kind(payload: str, ship: str) -> str | None:
    """把一条 ``/xxx`` 查询体解析成口径；不像一次查询就返回 ``None``。

    有船名 + 有时间 = ``ship_recent``（单船近期）；只有船名（``/西西里``、
    ``/西西里水表``）= ``ship``（这艘船的生涯总表）；只有时间（``/30近期``、
    ``/recent``）= ``recent``；光秃秃的「水表 / 查水表 / 战绩」= ``account``（账号总表）。

    三者都没有就不认：``/哈哈`` 这种随手发一个斜杠词的交回正常聊天路径。
    """
    if not _simple_payload_ok(payload):
        return None
    days = _days_in_text(payload)
    has_ship = bool(ship) and not _SHIP_CONJUNCTION_RE.search(ship)
    if days:
        return "ship_recent" if has_ship else "recent"
    if _query_cue_only(payload):
        return "account"
    if has_ship:
        return "ship"
    return None


def _simple_query_payload(text: str) -> str | None:
    """整条消息是不是单独一条 ``/查询``；是就返回查询体。

    只剥 @ 标记、不动正文：「@群友/西西里recent30」→ ``西西里recent30``。
    除这一个 / 片段之外还有别的内容就返回 None（「单独的一条消息」才触发）。
    """
    value = _strip_at_tokens(text or "").strip()
    if not value:
        return None
    match = _SIMPLE_PREFIX_RE.match(value)
    if not match:
        return None
    payload = match.group("payload").strip()
    if not payload or "/" in payload or "／" in payload:
        return None
    return payload


# ── Tool 参数声明 ────────────────────────────────────────────────
# ToolParameterInfo 的 description 就是模型看到的全部信息，写得越具体越好。


def _p_str(
    name: str, description: str, required: bool = False
) -> ToolParameterInfo:
    return ToolParameterInfo(
        name=name,
        param_type=ToolParamType.STRING,
        description=description,
        required=required,
    )


def _p_int(
    name: str, description: str, required: bool = False
) -> ToolParameterInfo:
    return ToolParameterInfo(
        name=name,
        param_type=ToolParamType.INTEGER,
        description=description,
        required=required,
    )


def _p_server(required: bool = False, description: str | None = None) -> ToolParameterInfo:
    return _p_str(
        "server",
        description
        or "服务器名：亚服 / 国服 / 欧服 / 美服 / 俄服。查别人时必须和 nickname 一起给；查自己时两个都别填。",
        required,
    )


def _p_nickname(
    required: bool = False, description: str | None = None
) -> ToolParameterInfo:
    return _p_str(
        "nickname",
        description
        or "游戏昵称（与游戏内完全一致，不是 QQ 昵称/群名片）。查别人时必须和 server 一起给；"
        "查自己（没在句子里点名别人）时两个都别填，尤其不要把群名片填进来。",
        required,
    )


def _p_ship(required: bool = False) -> ToolParameterInfo:
    return _p_str("ship", "战舰名称，中英文均可（如 俾斯麦 / Bismarck）；换皮船要带标记（北卡CLR）", required)


def _p_days() -> ToolParameterInfo:
    return _p_int(
        "days",
        "统计最近多少天（默认 7，最大 366=一年）。用户提了时间就必须显式填，不要省："
        "今天=1、昨天=2、这几天=3、这周/一周=7、这一个月/30 天=30、两个月=60、三个月=90、"
        "半年=180、一年=360。",
    )


def _p_select() -> ToolParameterInfo:
    return _p_int(
        "select_index",
        "上一轮返回了多个候选时，用户选定的序号（从 1 开始）；没有候选就不要填",
    )


def _p_battle_mode() -> ToolParameterInfo:
    return _p_str(
        "battle_mode",
        "只关注某个模式时填写：随机 / 单野 / 自行车 / 三轮车 / 排位。不填则返回全部模式",
    )

# 不需要 token 就能用的指令：空参数（等同 help）与帮助本身
_TOKEN_FREE_COMMANDS = {"", "help", "帮助"}

# 允许「@机器人 /wws xxx」「/wws xxx」「wws xxx」三种写法。
# 指令词先收一小池口语词进正则，具体哪些生效由配置 `trigger.command_aliases` 决定：
# 正则只负责把「可能是指令」的消息捞出来，配置决定要不要接（见 _activation_ok）。
# 前缀要连原始 @ 码一起吃掉：上游约定 @ 由接入端处理，但接入端若把
# `[CQ:at,qq=...]` / `<@!...>` 原样透传，正则从 `[` / `<` 起就匹配不上，整条指令不触发。
_WAKE_WORD_POOL = ("wws", "水表", "战绩", "战舰世界", "战舰")
_AT_PREFIX = r"(?:@\S+|\[CQ:at[^\]]*\]|<@!?[^>\s]*>)"
WWW_PATTERN = (
    r"^\s*(?:" + _AT_PREFIX + r"\s+)*[/!！／]?\s*(?P<word>"
    + "|".join(_WAKE_WORD_POOL)
    + r")(?:\s+(?P<rest>.*))?$"
)
SELECT_PATTERN = r"^\s*(?P<index>\d{1,2})\s*$"

_WWW_RE = re.compile(WWW_PATTERN)
_SELECT_RE = re.compile(SELECT_PATTERN)

# 「这条消息 @ 了我吗」的记录有效期。入站消息一定先过 Hook 再进模型，
# 正常情况下工具执行时记录只有几秒；留长一点只为兜住模型思考/排队的延迟。
_AT_STATE_TTL = 180.0

# 「会话最近发送者」的兜底有效期，比 @ 记录短：只用来补宿主的群会话 user_id。
_SENDER_STATE_TTL = 120.0

# 「最近一条水表查询的发送者」有效期：规划/排队经常拖到两分钟以上（实测 140s），
# 用上面那个 120s 的短 TTL 会让「查我的」在工具执行时丢了发送者，回一句「没绑定」。
# 只记录带查询意图的消息，普通闲聊不会把它顶掉。
_PENDING_QUERY_TTL = 600.0

# 「会话最近一条消息原文」的有效期：工具拿不到触发消息，只能靠 Hook 记下的原文
# 兜住模型漏填的 days（「西西里90天」被丢了 days 就会退回默认 7 天）。
_SENT_TEXT_TTL = 600.0

# 「上一轮返回了候选列表」的有效期：模型会凭空填 select_index，
# 没有候选时带序号会走 hikari 的二次选择分支，报「找不到船」。
_SELECT_STATE_TTL = 600.0

# @ 群友消息里要剥掉的闲聊词，剩下的才是船名候选
_MENTION_NOISE_WORDS = (
    "帮我", "帮忙", "麻烦", "查询", "查一下", "查查", "查下", "看看", "看下", "看一下",
    "他们的", "他的", "她的", "你的", "我的", "水表", "战绩", "数据", "胜率", "单船",
    "最近", "近期", "这位", "那位", "群友", "大佬", "一下", "怎么样", "如何", "多少",
    "他", "她", "你", "我", "是", "在", "请", "查", "ta", "TA",
)

# 船名里要剥掉的英文参数词；按整词匹配，AL / ARP / HSF 这类联动船前缀不在表内
_EN_NOISE_RE = re.compile(
    r"(?<![A-Za-z])(?:recent|career|single|solo|overall|total|all|ship|wows|wws|"
    r"water|meter|stats?|avg|winrate|wr|pr)(?![A-Za-z])",
    re.IGNORECASE,
)

# 时间词：先按数字剥（90天 / 两个月），再按固定说法剥
_TIME_WORDS = (
    "近一年", "十二个月", "六个月", "近半年", "半年", "一年",
    "近两个月", "两个月", "三个月", "一个月", "这个月", "上个月", "本月", "这周", "本周", "上周",
    "一周", "这礼拜", "这几天", "三天", "今天", "今日", "昨天", "昨日", "当天", "近几天",
)

# 文本里的 @ 标记形如 @<昵称:QQ号>，判断关键词前先抹掉，免得昵称里带「战绩」误命中
_AT_MARKUP_RE = re.compile(r"@<[^>]*>")
# 兜底：纯文本的「@昵称」也要抹掉，否则会被当成船名的一部分
_AT_MENTION_RE = re.compile(r"@[^\s@]+")
# 原始 @ 码：OneBot 的 [CQ:at,qq=...] 与 <@123> / <@!123>。上游明确要求这类 token
# 不能进 command_text（会被当普通参数，把 AID 挤到服务器位回「服务器名输入错误」）。
_AT_CQ_RE = re.compile(r"\[CQ:at[^\]]*\]", re.IGNORECASE)
_AT_ANGLE_RE = re.compile(r"<@!?[^>\s]*>")


def _strip_at_tokens(text: str) -> str:
    """抹掉文本里所有形式的 @ 提及，收口「@ 由接入端处理」这条约定。"""
    if not text:
        return ""
    value = _AT_MARKUP_RE.sub(" ", text)
    value = _AT_CQ_RE.sub(" ", value)
    value = _AT_ANGLE_RE.sub(" ", value)
    value = _AT_MENTION_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()

# 配置里关键词一栏被清空时的兜底
_DEFAULT_MENTION_KEYWORDS = ("水表", "战绩", "查一下", "查查", "查询")

# 一条消息算不算「战绩查询」：用来记下最近的查询发送者（见 _PENDING_QUERY_TTL）
_QUERY_HINT_RE = re.compile(r"水表|战绩|场均|胜率|recent|wws", re.I)

# 明显是聊天、不是查询的词。用来把「/recent 怎么用」「我今天打了一局」
# 「最近手感不好」这类句子挡在查询之外——只靠剥词是剥不干净的。
_QUERY_NOISE_RE = re.compile(
    r"怎么|如何|为什么|什么|吗|呢|吧|啊|呀|的|了|是|在|用|说|问|看|帮|给|替|"
    r"觉得|感觉|手感|打|玩|局|把|求|教|试"
)
_SIMPLE_PAYLOAD_MAX = 24

# 配置关键词之外、语义上明确是查询的句式。裸「最近」不算：
# 「最近手感不好」这种闲聊也会被当成查询，平白往群里甩一张水表。
_EXTRA_QUERY_CUES = (
    re.compile(r"(?<![A-Za-z])recent(?![A-Za-z])", re.IGNORECASE),
    # 「近期」不带数字也算：默认 1 天（见 _days_in_text）
    re.compile(r"近期"),
    re.compile(r"查\s*[我咱俺]"),
    # 「近188天4501」：近/最近 带明确时间单位才算。只认 天/周/个月，
    # 「最近30把西西里」是局数不是天数，别误触发
    re.compile(r"(?:近|最近)\s*(?:\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?:天|周|个\s*月)"),
)

# 只有用户原话真的提到「绑定」才允许 wws_bind 落地：
# 实测模型在私聊里凭空调了一次 wws_bind，把默认账号从 A 换成了 B，
# 之后的「查我」全都查到了别人号上，用户看到的是「没有出战记录」。
_BIND_INTENT_RE = re.compile(r"绑定|绑|bind", re.IGNORECASE)

# 「@机器人 + 自己查自己」的判定：出现「我 / 咱 / 俺」才算自己；
# 出现「帮 / 给 / 替 / 他 / 她 / 群友」这类转指就不抢，交回模型，
# 免得把「帮我查下张三的」认成查发送者本人。
_SELF_QUERY_RE = re.compile(r"[我咱俺]")
_OTHER_PERSON_RE = re.compile(r"帮|给|替|他|她|群友|这位|那位|别人|某人")


def _is_bare_query(text: str) -> bool:
    """剥掉关键词与标点后什么都不剩：``@我 水表`` / ``查一下战绩`` 都是在查自己。"""
    value = _strip_at_tokens(text or "")
    for word in (*_MENTION_NOISE_WORDS, *_DEFAULT_MENTION_KEYWORDS):
        value = value.replace(word, " ")
    value = re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)
    return not value


class _MentionOutcome(NamedTuple):
    """@ 查询的一次结果：图 + 实际用的口径 + 要补发给用户的说明。"""

    hikari: Any
    kind: str
    days: int | None = None
    note: str = ""


def _window_empty(hikari: Any) -> bool:
    """单船近期接口在窗口内没有战绩时返回「当前日期无recent记录」。"""
    data = getattr(getattr(hikari, "Output", None), "Data", None)
    return isinstance(data, str) and "无recent记录" in data


# 单船查询「这船本身查不到数据」的上游说法：
# 找不到船（名字没匹配上）/ 该战舰数据不存在（上游没有这船的账号数据）/ 查询不到战绩数据（一场没打过）
_SHIP_MISSING_MARKERS = ("找不到船", "该战舰数据不存在", "查询不到战绩数据")


def _ship_missing(hikari: Any) -> bool:
    data = getattr(getattr(hikari, "Output", None), "Data", None)
    return isinstance(data, str) and any(m in data for m in _SHIP_MISSING_MARKERS)


def _ship_missing_text(ship: str, hikari: Any) -> str:
    """把「找不到船 / 该战舰数据不存在」翻成用户能判读的一句话。

    ▍为什么两句都要带「换默认账号」
    实测最常见的成因不是船名写错，而是默认账号被换成了没有这艘船的号：
    同一条船，旧默认账号 success、新默认账号「该战舰数据不存在」。
    只回「船名可能写错了」，用户会一直换写法，永远查不出来。
    """
    data = str(getattr(getattr(hikari, "Output", None), "Data", "") or "")
    if "找不到船" in data:
        # 上游的 userShip 搜索只看「这个号名下有的船」：没买过 / 一场没开过的船
        # 同样回「找不到船」，和船名写错分不出来。所以两种原因并列说，别只怪船名。
        return (
            f"「{ship}」在当前默认账号下查不到：可能是船名写错，"
            "也可能这个号没买过 / 没开过这艘船（没打过的船不在单船查询里）。"
            "换完整船名 / 英文名再试；绑了多个号的话发【/wws 切换绑定】换默认账号再查~"
        )
    return (
        f"「{ship}」没有战绩数据：当前默认账号可能没买过、没开过这艘船，或上游还没有这船的数据。"
        "绑了多个号的话发【/wws 切换绑定】换默认账号再查~"
    )


def _observed_identity(hikari: Any) -> tuple[str, str]:
    """从查询结果里取**真实**查到的账号 ``(server, userName)``。

    ▍为什么不用身份缓存
    me 模式的图片是上游按「当前默认绑定」现算的，而说明里的名字来自 5 分钟缓存的
    ``bind/list``。用户中途换了默认账号时，说明和图就会是两个号——实测被人当成
    「查出来的是别人」。这里直接取结果里的 ``userInfo``，说明/日志与图永远同源。
    """
    if getattr(hikari, "Status", "") != "success":
        return "", ""
    data = getattr(getattr(hikari, "Output", None), "Data", None)
    if not isinstance(data, dict):
        return "", ""
    info = data.get("userInfo")
    info = info if isinstance(info, dict) else data
    name = str(info.get("userName") or "").strip()
    if not name:
        return "", ""
    return str(info.get("server") or "").strip(), name


# @全体成员 的 target_user_id 各适配器写法不一，这些都当「不是某个人」跳过
_MENTION_ALL_TARGETS = frozenset({"all", "0", "-1", "everyone", "全体成员"})

# @ 查询的限流窗口（与配置里的「每群每分钟上限」配套）
_MENTION_WINDOW = 60.0

# 机器人自己的名字/别名以 MaiBot 的 bot_config.toml 为准（[bot] nickname + alias_names），
# 插件不再单独维护一份。按文件 mtime 缓存，改了配置不用重载插件。
_BOT_CONFIG_CANDIDATES = ("/MaiBot/config/bot_config.toml", "/MaiMBot/config/bot_config.toml")
_bot_names_cache: tuple[str, float, frozenset[str]] | None = None
_bot_config_missing_logged = False


def _bot_config_path() -> Path | None:
    env = os.environ.get("MAIBOT_BOT_CONFIG", "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "config" / "bot_config.toml")
    candidates.extend(Path(p) for p in _BOT_CONFIG_CANDIDATES)
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _bot_config_names() -> set[str]:
    """读 MaiBot 配置里的 ``[bot] nickname`` 与 ``alias_names``。"""
    global _bot_names_cache, _bot_config_missing_logged
    path = _bot_config_path()
    if path is None:
        if not _bot_config_missing_logged:
            _bot_config_missing_logged = True
            logger.warning("[wows] 没找到 MaiBot 的 bot_config.toml，机器人名字只靠群里学到的群名片")
        return set()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return set()
    if _bot_names_cache and _bot_names_cache[0] == str(path) and _bot_names_cache[1] == stamp:
        return set(_bot_names_cache[2])

    names: set[str] = set()
    if tomllib is not None:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            bot = data.get("bot") if isinstance(data, dict) else None
            if isinstance(bot, dict):
                nickname = str(bot.get("nickname") or "").strip()
                if nickname:
                    names.add(nickname)
                for item in bot.get("alias_names") or []:
                    alias = str(item or "").strip()
                    if alias:
                        names.add(alias)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[wows] 读取机器人名字失败 {path}: {e!r}")
    _bot_names_cache = (str(path), stamp, frozenset(names))
    return names


# ── 指令参数容错 ──────────────────────────────────────────────────
#
# 上游解析两条硬约定，用户不可能事先知道：
# 1. 游戏昵称带空格时必须写成 `cn (alice smith)`——`cn alice smith` 会被判成
#    「未识别的指令」（账号查询只接受恰好一个昵称 token）；
# 2. 按 AID 绑定的关键字是 `特殊绑定`，`bind cn 123456789` 会被当成昵称去搜。
# 群里实测就卡在这两处，所以在进 hikari 之前/解析报错之后各兜一层。

# hikari 报「参数/身份解析失败」的说法（只有这些才值得换个写法重试）
_IDENTITY_ERROR_MARKERS = (
    "未识别的指令",
    "您似乎准备用游戏昵称查询水表",
    "参数似乎输错了呢",
    "请检查参数中是否包含服务器和游戏昵称",
)

# 按 AID 绑定：这几个词后面跟纯数字时改写成「特殊绑定」
_BIND_KEYWORDS = frozenset({"bind", "绑定", "set", "特殊绑定"})
_AID_MIN_LEN = 6
# 自动补括号时，昵称最多占几个 token（防止把整句都吞进昵称）
_WRAP_GROUP_MAX_TOKENS = 6

_server_tokens_cache: set[str] | None = None
_command_keywords_cache: set[str] | None = None


def _server_tokens() -> set[str]:
    """hikari 认的全部服务器关键词（小写）。"""
    global _server_tokens_cache
    if _server_tokens_cache is None:
        try:
            from hikari_core.core.constants import servers

            _server_tokens_cache = {str(kw).casefold() for item in servers for kw in item.keywords}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wows] 读取服务器关键词失败: {e!r}")
            _server_tokens_cache = set()
    return _server_tokens_cache


def _command_keywords() -> set[str]:
    """hikari 全部一级/二级指令词（小写），用来把昵称词和指令词分开。"""
    global _command_keywords_cache
    if _command_keywords_cache is None:
        words: set[str] = set()
        try:
            from hikari_core.commands.router import first_command_list

            stack = list(first_command_list)
            while stack:
                com = stack.pop()
                words.update(str(kw).casefold() for kw in (com.keywords or ()))
                stack.extend(com.second_select or [])
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wows] 读取指令关键词失败: {e!r}")
        _command_keywords_cache = words
    return _command_keywords_cache


def _identity_param_error(hikari: Any) -> bool:
    """hikari 是否因为身份/参数写法不认而报错（而不是上游查不到）。"""
    if getattr(hikari, "Status", "") != "error":
        return False
    data = str(getattr(getattr(hikari, "Output", None), "Data", "") or "")
    return any(m in data for m in _IDENTITY_ERROR_MARKERS)


def _rewrite_aid_bind(text: str) -> str | None:
    """`bind cn 123456789` → `特殊绑定 cn 123456789`；不是 AID 绑定就返回 None。"""
    tokens = text.split()
    keyword_at = next(
        (i for i, t in enumerate(tokens) if t.casefold() in _BIND_KEYWORDS), None
    )
    if keyword_at is None:
        return None
    if not any(t.isdigit() and len(t) >= _AID_MIN_LEN for t in tokens):
        return None
    tokens[keyword_at] = "特殊绑定"
    return " ".join(tokens)


def _has_bind_keyword(text: str) -> bool:
    """这条指令里有没有绑定类关键字（bind / 绑定 / set / 特殊绑定）。"""
    return any(t.casefold() in _BIND_KEYWORDS for t in text.split())


_BIND_USAGE = (
    "绑定要带上服务器：`/wws bind cn 昵称`（cn 国服 / asia 亚服 / eu 欧服 / na 美服 / ru 俄服），"
    "昵称带空格时整体加半角括号。\n"
    "用 AID 绑定：`/wws 特殊绑定 cn <AID>`。"
)


def _clean_command_args(text: str) -> str:
    """去掉参数里混进来的 @ 标记/昵称/原始 @ 码。

    实测 ``@机器人 wws 特殊绑定 123456789 @某群友``：末尾 @ 群友只是
    为了提醒对方，却会当成参数交给 hikari，把 AID 挤到服务器位上，回一句
    「服务器名输入错误」。@ 群友在指令参数里永远没有意义，直接剥掉；
    ``[CQ:at,qq=...]``、``<@!...>`` 这类原始 @ 码同理。
    """
    return _strip_at_tokens(text)


def _parse_wake_aliases(raw: str) -> set[str]:
    """配置里的指令词；只接受内置词池，写错时退回 ``wws``。"""
    words = {w.strip().casefold() for w in re.split(r"[,，、\s]+", raw or "") if w.strip()}
    valid = {w for w in words if w in _WAKE_WORD_POOL}
    return valid or {"wws"}


def _bind_param_hint(text: str) -> str | None:
    """绑定族参数不完整/服务器写错时给出可照做的提示；正常返回 ``None``。

    hikari 对 ``特殊绑定 123456789`` 这种缺服务器的写法只会回「服务器名输入错误」，
    用户不知道少写了什么。这里先按语法检查一遍，直接把正确写法回给用户。
    """
    tokens = text.split()
    keyword_at = next(
        (i for i, t in enumerate(tokens) if t.casefold() in _BIND_KEYWORDS), None
    )
    if keyword_at is None:
        return None
    keyword = tokens[keyword_at].casefold()
    rest = tokens[keyword_at + 1:]
    if not rest:
        return _BIND_USAGE
    if keyword == "特殊绑定" and rest[0].isdigit() and len(rest[0]) >= _AID_MIN_LEN:
        return "特殊绑定要写成 `/wws 特殊绑定 <服务器> <AID>`，例如 `/wws 特殊绑定 cn 123456789`；只给 AID 不行。"
    if rest[0].casefold() not in _server_tokens():
        return f"「{rest[0]}」不是服务器名。服务器可写 cn/国服、asia/亚服、eu/欧服、na/美服、ru/俄服。\n" + _BIND_USAGE
    if keyword == "特殊绑定":
        if not any(t.isdigit() and len(t) >= _AID_MIN_LEN for t in rest[1:]):
            return "特殊绑定要写成 `/wws 特殊绑定 <服务器> <AID>`，例如 `/wws 特殊绑定 cn 123456789`。"
        return None
    if len(rest) < 2:
        return _BIND_USAGE
    return None


def _special_space_name(text: str) -> tuple[str, str] | None:
    """绑定指令里昵称含全角空格等非半角空白时，返回 ``(服务器, 原始昵称)``。

    hikari 按空白切词（U+3000 也算空白），括号重拼又只接半角空格，
    ``bind cn Alice　Smith`` 到上游会变成 ``Alice Smith``，精确搜索必然「用户不存在」。
    这种昵称只能先按原字符串查 AID，再改走 ``特殊绑定``。
    """
    spans = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", text or "")]
    if len(spans) < 3:
        return None
    servers = _server_tokens()
    keyword_at = next(
        (i for i, (_, _, t) in enumerate(spans) if t.casefold() in _BIND_KEYWORDS), None
    )
    server_at = next(
        (i for i, (_, _, t) in enumerate(spans) if t.casefold() in servers), None
    )
    if keyword_at is None or server_at is None:
        return None
    name_at = [i for i in range(len(spans)) if i not in (keyword_at, server_at)]
    if not name_at or name_at[-1] - name_at[0] != len(name_at) - 1:
        return None
    raw = text[spans[name_at[0]][0]:spans[name_at[-1]][1]]
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip(" ")
    if not raw or not any(ch.isspace() and ch != " " for ch in raw):
        return None
    return spans[server_at][2], raw


async def _bind_by_aid(server: str, nickname: str) -> str | None:
    """昵称带非半角空白时换成 ``特殊绑定 <服务器> <AID>``；不适用或查不到返回 None。"""
    if not server or not nickname:
        return None
    if not any(ch.isspace() and ch != " " for ch in nickname):
        return None
    account_id = await resolve_account_id(server, nickname)
    if not account_id:
        return None
    return f"特殊绑定 {server} {account_id}"


def _paren_retry_text(text: str) -> str | None:
    """给「服务器 + 多词昵称」补括号：`cn alice smith` → `cn (alice smith)`。

    只在 hikari 报参数错之后调用。把 token 分成「数据词」和「服务器词/指令词/
    天数日期」，取最长的连续数据词片段（≥2 个）当昵称包起来；其余原样保留，
    `cn alice smith recent 7` 也就变成 `cn (alice smith) recent 7`。
    """
    tokens = text.split()
    if len(tokens) < 3 or "(" in text:
        return None
    servers = _server_tokens()
    if not any(t.casefold() in servers for t in tokens):
        return None
    keywords = _command_keywords()

    def is_data(token: str) -> bool:
        low = token.casefold()
        if low in servers or low in keywords or low == "me":
            return False
        if token.isdigit() and len(token) <= 3:
            return False
        try:
            time.strptime(token, "%Y-%m-%d")
            return False
        except ValueError:
            return True

    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, token in enumerate(tokens):
        if is_data(token):
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(tokens)))
    runs = [r for r in runs if r[1] - r[0] >= 2]
    if not runs:
        return None
    head, tail = max(runs, key=lambda r: r[1] - r[0])
    if tail - head > _WRAP_GROUP_MAX_TOKENS:
        return None
    grouped = " ".join(tokens[head:tail])
    return " ".join([*tokens[:head], f"({grouped})", *tokens[tail:]])


class WowsYuyukoPlugin(MaiBotPlugin):
    """战舰世界战绩查询插件。"""

    config_model = WowsSettings

    def __init__(self) -> None:
        super().__init__()
        self.selection = SelectionManager()
        self.sender = OutputSender()
        self._runtime: CoreRuntime | None = None
        self._guard: ToolGuard | None = None
        self._tools: ToolExecutor | None = None
        self._tool_ctx_logged = False
        # (platform, "g:群号" / "u:用户" / "s:会话") -> (是否 @ 了机器人, 发送者 ID, 记录时刻)
        self._at_state: dict[tuple[str, str], tuple[bool, str, float]] = {}
        # (platform, "群/用户/会话") -> (发送者 ID, 消息原文, 记录时刻)
        self._sent_text: dict[tuple[str, str], tuple[str, str, float]] = {}
        # (platform, "群/用户/会话") -> (发送者 ID, 查询原文, 记录时刻)：只记查询消息，
        # 工具执行时用来找回「查我的」到底是谁问的（普通闲聊不会覆盖它）
        self._pending_query: dict[tuple[str, str], tuple[str, str, float]] = {}
        # 消息里 @ 机器人时学到的本群昵称，供「文字里叫机器人名字」判定使用
        self._learned_bot_names: set[str] = set()
        # stream_id -> 候选列表出现时刻（模型乱填 select_index 时用来兜底）
        self._pending_select: dict[str, float] = {}
        self._at_gate_logged = False
        # 「群里 @ 群友查水表」的限流记录与后台任务
        self._mention_hits: dict[str, deque[float]] = {}
        self._mention_tasks: set[asyncio.Task] = set()

    @property
    def runtime(self) -> CoreRuntime:
        if self._runtime is None:
            raise RuntimeError("插件尚未完成初始化")
        return self._runtime

    @property
    def guard(self) -> ToolGuard:
        if self._guard is None:
            raise RuntimeError("插件尚未完成初始化")
        return self._guard

    @property
    def tools(self) -> ToolExecutor:
        if self._tools is None:
            raise RuntimeError("插件尚未完成初始化")
        return self._tools

    # ── 生命周期 ──────────────────────────────────────────────────

    async def on_load(self) -> None:
        """下发配置并启动 hikari-core。"""
        if not self._token_configured():
            logger.warning(
                "[wows] 未配置 yuyuko API Token：除 /wws help 外的指令都会提示先配置；"
                "申请与填写方式见 README 的「获取并填写 Token」一节"
            )
        self._runtime = CoreRuntime(self.config, self.ctx.paths.data_dir)
        self._build_tool_layer()
        await self._runtime.start()

    async def on_unload(self) -> None:
        """插件被禁用/重载时清理临时资源。"""
        try:
            logger.info("[wows] 开始销毁临时资源")
            self.selection.clear()
            self._at_state.clear()
            self._mention_hits.clear()
            for task in list(self._mention_tasks):
                task.cancel()
            self._mention_tasks.clear()
            if self._guard is not None:
                self._guard.reset()
            if self._runtime is not None:
                await self._runtime.shutdown()
            logger.info("[wows] 临时资源销毁完成")
        except Exception as e:
            logger.exception(f"[wows] 清理临时资源失败: {e}")

    async def on_config_update(
        self, scope: str, config_data: dict[str, Any], version: str
    ) -> None:
        """热更新插件自身配置（重新下发 hikari-core 配置）。"""
        del config_data, version
        if scope != "self" or self._runtime is None:
            return
        # 配置对象被整体替换，工具层持有的是旧引用，必须重建
        self._build_tool_layer()
        await self._runtime.apply_config(self.config)

    def _build_tool_layer(self) -> None:
        """按当前配置重建开关/限流与执行器（热更新会替换 self.config）。"""
        self._guard = ToolGuard(self.config.llm_tool)
        self._tools = ToolExecutor(
            runtime=self.runtime,
            sender=self.sender,
            guard=self._guard,
            config=self.config.llm_tool,
        )

    # ── 指令 ──────────────────────────────────────────────────────

    @Command(
        name="wws",
        description="战舰世界战绩查询（/wws help 查看用法）",
        pattern=WWW_PATTERN,
    )
    async def handle_wws(self, **kwargs: Any) -> tuple[bool, str, int]:
        """查询并渲染战绩图片。"""
        stream_id = str(kwargs.get("stream_id") or "")
        if not stream_id:
            return False, "", 0

        groups = self._wws_groups(kwargs)
        if not self._activation_ok(kwargs, groups):
            # 命中了指令词但不符合配置的唤起方式：交回宿主，按普通消息处理
            return False, "", 0
        rest = _clean_command_args(self._wws_args(kwargs))
        # MaiBot 只记录「命令执行成功: wws」不带参数，出问题时全靠这行还原用户输入
        logger.info(
            f"[wows] 指令 {rest!r} user={kwargs.get('user_id')} group={kwargs.get('group_id')}"
        )
        if not self._token_configured() and not self._is_token_free(rest):
            logger.warning("[wows] 未配置 wows_token，已拦截指令并提示获取方式")
            await reply_text(self.ctx, stream_id, missing_token_hint())
            return True, "", 2

        if not self.runtime.is_ready():
            # 首次启动要下模板/资源，这期间别让指令静默卡死
            await reply_text(self.ctx, stream_id, "正在准备渲染资源，请稍等一会儿再查询哦~")
            return True, "", 2

        if self.selection.is_pending(stream_id):
            await reply_text(self.ctx, stream_id, "上一条查询还在等你回复序号，请先选完或等待超时~")
            return True, "", 2

        async with self.runtime.render_lock:
            try:
                async def _init(command_text: str):
                    return await init_hikari(
                        command_text=command_text,
                        platform=PLATFORM,
                        PlatformId=str(kwargs.get("user_id") or ""),
                        BotId=self._self_id(kwargs),
                        GroupId=str(kwargs.get("group_id") or "") or None,
                    )

                query_text = rest
                rewritten = _rewrite_aid_bind(rest)
                if rewritten and rewritten != rest:
                    logger.info(f"[wows] 指令 {rest!r} 按 AID 绑定改写成 {rewritten!r}")
                    query_text = rewritten
                elif _has_bind_keyword(query_text):
                    special = _special_space_name(query_text)
                    by_aid = await _bind_by_aid(*special) if special else None
                    if by_aid:
                        logger.info(
                            f"[wows] 指令 {query_text!r} 昵称含全角空格，按 {by_aid!r} 执行"
                        )
                        query_text = by_aid
                    elif special:
                        # 全角空格昵称按原串都查不到，就别再走半角空格（上游必然查不到）
                        server, name = special
                        logger.info(f"[wows] 指令 {query_text!r} 全角空格昵称查不到 AID")
                        await reply_text(
                            self.ctx,
                            stream_id,
                            f"没查到「{name}」这个账号（{server}），请确认服务器和昵称是否写对；"
                            f"也可以直接提供 AID：`/wws 特殊绑定 {server} <AID>`。",
                        )
                        return True, "", 2
                    else:
                        # 绑定只取第一个 token 当昵称：`bind cn alice smith` 会「成功」地绑成
                        # `alice` 并把默认账号也换掉，所以这一族必须提前补括号，不能等报错
                        grouped = _paren_retry_text(query_text)
                        if grouped and grouped != query_text:
                            logger.info(f"[wows] 指令 {query_text!r} 含多词昵称，按 {grouped!r} 执行")
                            query_text = grouped
                bind_hint = _bind_param_hint(query_text)
                if bind_hint:
                    logger.info(f"[wows] 指令 {query_text!r} 绑定参数不完整，已回用法提示")
                    await reply_text(self.ctx, stream_id, bind_hint)
                    return True, "", 2
                hikari = await _init(query_text)
                if _identity_param_error(hikari):
                    # 昵称带空格却没加括号时上游只报「未识别的指令」，按括号形态再试一次
                    retry_text = _paren_retry_text(query_text)
                    if retry_text and retry_text != query_text:
                        retry = await _init(retry_text)
                        if retry.Status != "error":
                            logger.info(
                                f"[wows] 指令 {query_text!r} 参数无法解析，"
                                f"按 {retry_text!r} 重试成功"
                            )
                            hikari = retry
                if hikari.Status == "wait":
                    # 先登记状态槽再发选择界面，免得用户手快先回序号时状态还没就绪
                    state = self.selection.register(stream_id, hikari.Input.Select_Data)
                    await self.sender.send(self.ctx, stream_id, hikari)
                    index = await self.selection.wait(stream_id, state)
                    if index is None:
                        hikari = hikari.error("已超时退出")
                    else:
                        hikari.Input.Select_Index = index
                        hikari = await callback_hikari(hikari)
                logger.info(
                    f"[wows] 结果 status={hikari.Status} server={hikari.Input.Server} "
                    f"昵称={hikari.Input.AccountName!r} aid={hikari.Input.AccountId}"
                )
                await self._send_result(stream_id, hikari)
            except Exception as e:
                logger.exception(f"[wows] 指令处理异常: {e}")
                await reply_text(
                    self.ctx,
                    stream_id,
                    "呜呜呜发生了错误，可能是网络问题，如果过段时间不能恢复请联系麻麻哦~",
                )
        return True, "", 2

    @Command(
        name="wws_select",
        description="选择查询结果序号（/wws 交互流程内部使用）",
        pattern=SELECT_PATTERN,
    )
    async def handle_select(self, **kwargs: Any) -> tuple[bool, str, int]:
        """消费选择流程中用户回复的序号；没有待选择状态时放行给后续流程。"""
        stream_id = str(kwargs.get("stream_id") or "")
        if not stream_id or not self.selection.is_pending(stream_id):
            return False, "", 0

        async def reply(text: str) -> None:
            await reply_text(self.ctx, stream_id, text)

        consumed = await self.selection.consume_reply(
            stream_id, str(kwargs.get("text") or ""), reply
        )
        if consumed:
            return True, "", 2
        return False, "", 0

    # ── 结果发送 ──────────────────────────────────────────────────

    async def _send_result(self, stream_id: str, hikari) -> None:
        """发送渲染结果；昵称查不到时换成能自我纠错的提示。"""
        hint = account_not_found_hint(hikari)
        if hint:
            logger.info(f"[wows] 上游返回 {hikari.Output.Data!r}，改用排错提示")
            await reply_text(self.ctx, stream_id, hint)
            return
        await self.sender.send(self.ctx, stream_id, hikari)

    # ── 参数提取 ──────────────────────────────────────────────────

    @staticmethod
    def _wws_groups(kwargs: dict[str, Any]) -> dict[str, str]:
        """取正则分组（``word`` / ``rest``）；宿主没给 matched_groups 时自己再匹配一次。"""
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict):
            return {str(k): str(v or "") for k, v in groups.items()}
        match = _WWW_RE.match(str(kwargs.get("text") or ""))
        return {k: str(v or "") for k, v in match.groupdict().items()} if match else {}

    @staticmethod
    def _wws_args(kwargs: dict[str, Any]) -> str:
        """取 `/wws` 之后的参数文本。"""
        return WowsYuyukoPlugin._wws_groups(kwargs).get("rest", "").strip()

    def _activation_ok(self, kwargs: dict[str, Any], groups: dict[str, str]) -> bool:
        """按「唤起方式」配置判断这条消息算不算唤起插件。

        正则只负责把可能是指令的消息捞出来（见 ``_WAKE_WORD_POOL``），
        「用哪个词、要不要 / 前缀、群里要不要 @ 机器人」都由配置决定。
        不符合配置时返回 ``False``，消息按普通聊天继续处理。
        """
        cfg = getattr(self.config, "trigger", None)
        word = (groups.get("word") or "wws").casefold()
        aliases = _parse_wake_aliases(str(getattr(cfg, "command_aliases", "wws") or "wws"))
        if word not in aliases:
            logger.debug(f"[wows] 指令词 {word!r} 未在唤起配置中启用，交回宿主")
            return False

        message = kwargs.get("message")
        message = message if isinstance(message, dict) else {}
        group_id = str(kwargs.get("group_id") or self._group_id_of(message) or "").strip()
        if not group_id:
            # 私聊本身就是对机器人说的，不额外设门槛
            return True

        text = str(message.get("processed_plain_text") or kwargs.get("text") or "")
        if self._foreign_bot_at(message):
            logger.info(f"[wows] 这条消息 @ 的是别的机器人，指令不响应: {text[:60]!r}")
            return False
        cleaned = _strip_at_tokens(text).casefold()
        woke_by_at = self._message_is_at(message) or any(
            name.casefold() in cleaned for name in self._bot_names()
        )
        if bool(getattr(cfg, "require_at", False)) and not woke_by_at:
            logger.info(f"[wows] 唤起方式要求 @ 机器人，已放行: {text[:60]!r}")
            return False
        if bool(getattr(cfg, "require_prefix", False)):
            head = _strip_at_tokens(text)
            has_prefix = head[:1] in ("/", "!", "！", "／")
            if not has_prefix and not woke_by_at:
                logger.info(f"[wows] 唤起方式要求 / 前缀或 @ 机器人，已放行: {text[:60]!r}")
                return False
        return True

    @staticmethod
    def _self_id(kwargs: dict[str, Any]) -> str:
        """当前机器人账号（消息的接收账号）。"""
        message = kwargs.get("message")
        if isinstance(message, dict):
            return str(message.get("account_id") or "")
        return ""

    # ── 「仅 @ 时可用」门禁 ───────────────────────────────────────
    #
    # ▍为什么要绕一道消息 Hook
    # 工具调用只拿得到 stream_id / group_id / user_id / platform，**拿不到触发它的那条消息**，
    # 也就看不出用户有没有 @ 机器人。而群聊里 AI 可能自己决定去查战绩 —— 聊着别的话题
    # 突然甩一张水表就是误触发。所以在这里用 `chat.receive.after_process` 把每条入站消息
    # 的「是否 @ 了我」记下来，工具执行时按同一个群回查。
    #
    # 私聊不设限：私聊本身就是对机器人说的，没有 @ 的动作可用。

    @HookHandler(
        "chat.receive.after_process",
        name="wows_track_at",
        description="记录入站消息是否 @ 了机器人，供 AI 查询工具判断能否直接查战绩。",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        error_policy=ErrorPolicy.SKIP,
    )
    async def track_at_state(self, message: Any = None, **kwargs: Any) -> None:
        """记录一条入站消息的 @ 状态；任何异常都不影响消息主链。"""
        del kwargs
        if not isinstance(message, dict):
            return None
        try:
            at = self._message_is_at(message)
            sender = self._sender_id_of(message)
            now = time.monotonic()
            keys = self._at_state_keys(message)
            text = str(message.get("processed_plain_text") or "").strip()
            is_query = bool(text) and bool(_QUERY_HINT_RE.search(_strip_at_tokens(text)))
            if at:
                self._learn_bot_name(message)
            for key in keys:
                self._at_state[key] = (at, sender, now)
                if text:
                    self._sent_text[key] = (sender, text, now)
                if is_query and sender:
                    self._pending_query[key] = (sender, text, now)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wows] 记录 @ 状态失败: {e!r}")
        return None

    @staticmethod
    def _sender_id_of(message: dict[str, Any]) -> str:
        """取发送者平台 ID；群会话里工具上下文不带 user_id 时靠它兜底。"""
        info = message.get("message_info")
        info = info if isinstance(info, dict) else {}
        user = info.get("user_info")
        user = user if isinstance(user, dict) else {}
        sender = message.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        return str(
            user.get("user_id")
            or message.get("user_id")
            or message.get("sender_id")
            or sender.get("user_id")
            or ""
        ).strip()

    @staticmethod
    def _message_is_at(message: dict[str, Any]) -> bool:
        """判断入站消息是否 @ 了机器人。

        适配器（如 snowluma）已经把「at 目标 == 自己」算成 ``is_at``，这里再补几条兜底：
        ``additional_config.at_bot``、``mention_bot`` 段、``at`` 段的目标 ID 比对、
        以及文本里的 ``@<名字:账号>`` 标记。
        """
        if message.get("is_at") or message.get("is_mentioned"):
            return True

        info = message.get("message_info")
        info = info if isinstance(info, dict) else {}
        extra = info.get("additional_config")
        extra = extra if isinstance(extra, dict) else {}
        if extra.get("at_bot"):
            return True

        account_id = str(message.get("account_id") or "").strip()
        raw = message.get("raw_message")
        for seg in raw if isinstance(raw, list) else []:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "")
            if seg_type == "mention_bot":
                return True
            if seg_type != "at" or not account_id:
                continue
            data = seg.get("data")
            target = str(data.get("target_user_id") or "") if isinstance(data, dict) else ""
            if target and target == account_id:
                return True

        text = str(message.get("processed_plain_text") or "")
        if account_id and re.search(rf"@<[^>]*:{re.escape(account_id)}>", text):
            return True
        return False

    @staticmethod
    def _at_state_keys(message: dict[str, Any]) -> list[tuple[str, str]]:
        """一条消息可用来索引的会话键（群 / 发送者 / 会话 ID 都记一份）。

        工具侧只有 ``stream_id``、``group_id``、``user_id``，而消息侧能稳定拿到群号，
        所以两边都按同一套键名存查，任何一边缺失都还能命中。
        """
        platform = str(message.get("platform") or "").strip().lower()
        info = message.get("message_info")
        info = info if isinstance(info, dict) else {}
        group = info.get("group_info")
        group = group if isinstance(group, dict) else {}
        user = info.get("user_info")
        user = user if isinstance(user, dict) else {}

        keys: list[tuple[str, str]] = []
        group_id = str(group.get("group_id") or "").strip()
        user_id = str(user.get("user_id") or "").strip()
        session_id = str(message.get("session_id") or "").strip()
        if group_id:
            keys.append((platform, f"g:{group_id}"))
        if user_id:
            keys.append((platform, f"u:{user_id}"))
        if session_id:
            keys.append((platform, f"s:{session_id}"))
        return keys

    def _at_gate(self, kwargs: dict[str, Any], select_index: int | None) -> str | None:
        """群聊里没 @ 机器人时拦下工具调用；返回给模型的话术，放行返回 ``None``。

        - 开关关闭、私聊、或用户是在回答「选哪个」（``select_index``）时一律放行
        - 记录缺失（例如插件刚启动）也放行，只记一次日志，避免门禁静默失效
        """
        if not getattr(self.config.llm_tool, "require_at", True):
            return None
        if select_index is not None:
            return None

        group_id = str(kwargs.get("group_id") or "").strip()
        if not group_id:
            return None

        platform = str(kwargs.get("platform") or PLATFORM).strip().lower()
        keys: list[tuple[str, str]] = [(platform, f"g:{group_id}")]
        user_id = str(kwargs.get("user_id") or "").strip()
        stream_id = str(kwargs.get("stream_id") or "").strip()
        if user_id:
            keys.append((platform, f"u:{user_id}"))
        if stream_id:
            keys.append((platform, f"s:{stream_id}"))

        now = time.monotonic()
        latest: tuple[bool, str, float] | None = None
        for key in keys:
            record = self._at_state.get(key)
            if record is None or now - record[2] > _AT_STATE_TTL:
                continue
            if latest is None or record[2] > latest[2]:
                latest = record

        if latest is None:
            if not self._at_gate_logged:
                self._at_gate_logged = True
                logger.info(f"[wows] 未找到会话的 @ 记录，本次放行（检查消息 Hook 是否生效）: {keys[:2]}")
            return None
        if latest[0]:
            return None

        logger.info(f"[wows] 群聊消息未 @ 机器人，按配置拦截 AI 查询: group={group_id}")
        if self._simple_enabled():
            # 简易查询开着时，单独发一条 /西西里recent30 就能查，不需要 @ 我。
            # 再让模型回「得@我呀」既是错的，也是用户明确要求关掉的提示。
            return (
                "用户这条消息没有 @ 我，按插件配置不能主动查战绩。"
                "请不要输出任何战绩数据、不要调用查询工具，也不要提醒用户 @ 我、"
                "不要教用户怎么查——插件会自动处理「单独发一条 /船名recent天数」这类简易查询。"
                "这条消息按普通聊天处理，不要提战绩查询。"
            )
        return (
            "用户这条消息没有 @ 我，按插件配置不能主动查战绩。"
            "请不要输出任何战绩数据，也不要调用其它查询工具，"
            "只简短提醒用户「@我 并说明要查谁的战绩」即可。"
        )

    def _query_intent_gate(
        self, kwargs: dict[str, Any], select_index: int | None
    ) -> str | None:
        """校验触发工具的那条消息确实是查战绩；不是就拦下（放行返回 ``None``）。

        ▍为什么还要这一道
        ``_at_gate`` 只管「群聊有没有 @ 我」，管不住意图：规划模型会把
        「我今天打了一局」「最近手感不好」当成查询去调工具，而写回复的模型
        看不到工具结果，会顺口编数字。这里回查该会话最近一条消息，不是查询、
        且这个会话最近也没有查询消息时直接拦（同一会话 10 分钟内有查询就放行，
        保证「那亚服呢」这种追问能接上）。
        """
        cfg = getattr(self.config, "llm_tool", None)
        if cfg is None or not getattr(cfg, "require_query_intent", True):
            return None
        if select_index is not None:
            return None
        try:
            ctx = self._tool_ctx(kwargs)
            text = self._recent_text(ctx.stream_id, ctx.group_id, ctx.user_id)
        except Exception:  # noqa: BLE001
            return None
        if not text:
            # 没有记录（插件刚启动 / Hook 没拿到消息）：放行，避免门禁静默失效
            return None
        if _looks_like_query(text):
            return None

        now = time.monotonic()
        keys: list[tuple[str, str]] = []
        if ctx.stream_id:
            keys.append((PLATFORM.lower(), f"s:{ctx.stream_id}"))
        if ctx.user_id:
            keys.append((PLATFORM.lower(), f"u:{ctx.user_id}"))
        if ctx.group_id:
            keys.append((PLATFORM.lower(), f"g:{ctx.group_id}"))
        for key in keys:
            query = self._pending_query.get(key)
            if query and now - query[2] <= _PENDING_QUERY_TTL:
                return None

        logger.info(f"[wows] 消息不是查战绩，拦截 AI 查询: {text[:60]!r}")
        return (
            "用户最近这条消息不是在查战绩（只是聊天）。"
            "请不要调用查询工具、不要输出任何战绩数据；按聊天正常回应即可。"
        )

    # ── 群里 @ 群友查 TA 的水表 ───────────────────────────────────
    #
    # 用户 @ 一位群友并提到「水表 / 战绩」时，直接查**那位群友**绑定的游戏账号并回图，
    # 不需要 @ 机器人。
    #
    # ▍为什么也挂在 after_process，并且要 abort
    # 这条消息本来会交给 Maisaka 自由发挥，而我们要发的是插件自己渲染的水表图；
    # 不 abort 的话群里会出现「水表图 + AI 复述一遍」的双份回应。abort 后这条消息
    # 不再进模型，也就不会被当成聊天上下文（查询指令本来也不需要）。
    # 反过来，@ 了机器人或写的是 /wws 指令时一律放行，避免抢走原有的两条路径。
    #
    # ▍为什么查询丢到后台任务
    # 渲染一张水表要几秒，而 Hook 是阻塞的 —— 直接在 Hook 里等会卡住这个群的消息主链。

    @HookHandler(
        "chat.receive.after_process",
        name="wows_mention_query",
        description="群里 @ 了某位群友、同时叫到机器人并提到水表/战绩时，"
        "直接查 TA 绑定账号的战绩并回图；@机器人 查自己的水表也走这条确定性路径。",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def handle_mention_query(
        self, message: Any = None, **kwargs: Any
    ) -> dict[str, str] | None:
        """命中「@群友 + 关键词」或「单独一条 /查询」时发起查询并中止这条消息。"""
        del kwargs
        try:
            if not isinstance(message, dict):
                return None
            # @ 的是官方水表 bot（yuyuko助手 / kokomi…）就整条让开，指令路径同样不响应
            if self._foreign_bot_at(message):
                logger.info(
                    "[wows] 这条消息 @ 的是别的机器人，不响应: "
                    f"{_strip_at_tokens(str(message.get('processed_plain_text') or ''))[:60]!r}"
                )
                return None
            simple = self._simple_query_target(message)
            if simple is not None:
                return self._dispatch_simple(message, simple)
            if not self._mention_enabled():
                return None
            target = self._mention_target(message)
            self_query = False
            if target is None:
                # @ 群友那条路没命中时，再看是不是「@机器人 查我自己」
                target = self._self_query_target(message)
                self_query = target is not None
            if target is None:
                return None
            target_id, target_name = target
            stream_id = str(message.get("session_id") or "").strip()
            group_id = self._group_id_of(message)
            if not stream_id or not group_id:
                return None
            if not self._token_configured() or not self.runtime.is_ready():
                return None
            if not self._mention_rate_ok(group_id):
                return None
            kind, ship, days = self._mention_plan(message, target_name)
            # 这条消息被 abort 后不会进 mai_messages，原文只能靠这里留痕
            trigger_text = _strip_at_tokens(str(message.get("processed_plain_text") or ""))
            trigger_text = re.sub(r"\s+", " ", trigger_text).strip()
            logger.info(
                f"[wows] @ 查询触发: group={group_id} target={target_id} name={target_name!r} "
                f"类型={kind} 船={ship!r} 天数={days} 自查={self_query} 原文={trigger_text[:80]!r}"
            )
            self._spawn_mention_query(
                stream_id=stream_id,
                group_id=group_id,
                bot_id=str(message.get("account_id") or ""),
                target_id=target_id,
                target_name=target_name,
                kind=kind,
                ship=ship,
                days=days,
                self_query=self_query,
            )
            return {"action": "abort"}
        except Exception as e:  # noqa: BLE001
            # 任何意外都不拦消息，交回原来的处理链
            logger.warning(f"[wows] @ 查询预处理失败，已放行原消息: {e!r}")
            return None

    def _mention_enabled(self) -> bool:
        cfg = getattr(self.config, "mention_query", None)
        return bool(getattr(cfg, "enabled", False)) if cfg is not None else False

    def _mention_llm_unbound(self) -> bool:
        """未绑定提示是否交给模型现写（实验性，默认关闭）。"""
        cfg = getattr(self.config, "mention_query", None)
        return bool(getattr(cfg, "llm_unbound_reply", False)) if cfg is not None else False

    # ── 简易查询：单独一条 /<查询> ───────────────────────────────
    #
    # 用户要求「不 @ 也能查」：群里直接发 `/西西里recent30`、`/30近期`、`/水表`，
    # 或 `@群友/九月七日recent190`、`/recent@群友`，一条消息就是一次查询。
    #
    # ▍为什么要「整条消息正好是 /xxx」
    # 聊天里提到「/recent 怎么用」「我刚才发了 /西西里recent」都不该甩水表。
    # 所以先剥掉 @ 标记，剩下的必须正好是一个 `/` 开头的片段，多一个字都不算。
    # ▍为什么不抢正常指令
    # `/wws me recent`、`/ship recent 西西里 30` 这类是命令链的语法，开头词在黑名单里；
    # `@yuyuko助手 /wws me recent` 还会整条跳过（@ 的是别的机器人，见 _foreign_bot_at）。
    # ▍口径怎么定
    # 有船名 + 有时间 = 单船近期；只有船名（`/西西里`、`/西西里水表`）= 这艘船的生涯总表；
    # 只有时间（`/30近期`）= 账号近期；光秃秃一个「水表 / 查水表 / 战绩」= 账号总表（生涯）。
    # 三者都没有就不认——`/哈哈` 这种随手发一个斜杠词的，交回正常聊天路径。
    # ▍开关
    # 独立一组 `[simple_query] enabled`，并且要「群聊 @ 查询」或「AI 对话查询」至少开一个
    # 才生效：简易查询复用的正是那两条路径的查询与发图逻辑。

    def _simple_enabled(self) -> bool:
        """简易查询总开关：自身开着，且「@ 查询」或「AI 对话查询」至少开了一个。"""
        cfg = getattr(self.config, "simple_query", None)
        if cfg is None or not bool(getattr(cfg, "enabled", False)):
            return False
        mention = getattr(self.config, "mention_query", None)
        llm = getattr(self.config, "llm_tool", None)
        return bool(getattr(mention, "enabled", False) if mention is not None else False) or bool(
            getattr(llm, "enabled", False) if llm is not None else False
        )

    def _wake_aliases(self) -> set[str]:
        cfg = getattr(self.config, "trigger", None)
        return _parse_wake_aliases(str(getattr(cfg, "command_aliases", "wws") or "wws"))

    def _is_foreign_bot_name(self, name: str) -> bool:
        """这个显示名是不是别的机器人（官方水表 bot）；机器人自己的名字池不算。"""
        display = str(name or "").strip().casefold()
        if not display:
            return False
        if display in {own.casefold() for own in self._bot_names() if own}:
            return False
        return bool(_FOREIGN_BOT_NAME_RE.search(display))

    def _foreign_bot_at(self, message: dict[str, Any]) -> bool:
        """这条消息 @ 的是不是别的机器人（官方水表 bot）。

        群里 `@战舰世界yuyuko助手 /wws me recent` 是在跟官方 bot 说话，我们插一脚
        就会莫名其妙多出一张水表，所以指令路径与 @ / 简易查询路径一律不响应。
        同时也 @ 了我们自己时不算——那条消息明确是冲我们来的。
        """
        if not self._group_id_of(message):
            return False
        if self._message_is_at(message):
            return False
        return any(
            self._is_foreign_bot_name(name) for _target_id, name in self._at_targets(message)
        )

    def _simple_query_target(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """判断这条消息是不是简易查询；是就返回要查的目标与口径。"""
        if not self._simple_enabled() or message.get("is_notify"):
            return None
        text = str(message.get("processed_plain_text") or "")
        payload = _simple_query_payload(text)
        if not payload:
            return None
        head = payload.split()[0].casefold()
        if head in _SIMPLE_BLOCK_HEADS or head in self._wake_aliases():
            return None
        days = _days_in_text(payload)
        # 机器人自己的名字不能当船名（`/睦子米水表` 里的「睦子米」不是船）
        ship = _ship_in_text(self._strip_bot_names(payload))
        kind = _simple_payload_kind(payload, ship)
        if kind is None:
            return None
        if kind in ("recent", "account"):
            ship = ""
        if kind in ("ship", "account"):
            days = None
        group_id = self._group_id_of(message)

        targets = self._at_targets(message)
        if targets:
            if not group_id:
                return None
            # @ 的是别的机器人（官方水表 bot）就整条让开，即使同时也 @ 了我们
            if any(self._is_foreign_bot_name(name) for _target_id, name in targets):
                return None
            if len(targets) != 1:
                return None
            target_id, target_name = targets[0]
            self_query = False
        else:
            target_id = self._sender_id_of(message)
            target_name = self._sender_name_of(message) or "你"
            self_query = True
        if not target_id:
            return None
        return {
            "target_id": target_id,
            "target_name": target_name,
            "self_query": self_query,
            "kind": kind,
            "ship": ship,
            "days": days,
            # 严格触发：只要抠出了船名，就先拿上游百科校验这个船名真的存在。
            # 离线分不出 `/西西里` 和 `/今日摸鱼`、`/哈哈`，带时间的也可能是随手发个
            # 斜杠词（群里 `@某人 /今日摸鱼` 实测被当成「猪猪 · 最近 1 天」去查），
            # 查不到就整条静默忽略，连「未绑定」也不回。
            "verify_ship": bool(ship),
        }

    def _dispatch_simple(
        self, message: dict[str, Any], plan: dict[str, Any]
    ) -> dict[str, str] | None:
        """简易查询命中后起后台任务并发起 abort。"""
        stream_id = str(message.get("session_id") or "").strip()
        group_id = self._group_id_of(message)
        if not stream_id:
            return None
        if not self._token_configured() or not self.runtime.is_ready():
            return None
        if not self._mention_rate_ok(group_id or f"s:{stream_id}"):
            return None
        trigger_text = _strip_at_tokens(str(message.get("processed_plain_text") or ""))
        trigger_text = re.sub(r"\s+", " ", trigger_text).strip()
        logger.info(
            f"[wows] 简易查询触发: group={group_id or '-'} target={plan['target_id']} "
            f"name={plan['target_name']!r} 类型={plan['kind']} 船={plan['ship']!r} "
            f"天数={plan['days']} 自查={plan['self_query']} 校验船名={bool(plan.get('verify_ship'))} "
            f"原文={trigger_text[:80]!r}"
        )
        self._spawn_mention_query(
            stream_id=stream_id,
            group_id=group_id,
            bot_id=str(message.get("account_id") or ""),
            target_id=str(plan["target_id"]),
            target_name=str(plan["target_name"]),
            kind=str(plan["kind"]),
            ship=str(plan["ship"]),
            days=plan["days"],
            self_query=bool(plan["self_query"]),
            verify_ship=bool(plan.get("verify_ship")),
        )
        return {"action": "abort"}

    def _mention_target(self, message: dict[str, Any]) -> tuple[str, str] | None:
        """找出被 @ 的群友 ``(用户 ID, 显示名)``；不该触发时返回 ``None``。

        除了 @ 群友，还必须叫到机器人（@ 机器人，或文字里写了机器人的昵称），
        否则聊天里「@某人 你战绩好烂」这种话也会甩出一张水表。
        """
        if message.get("is_notify"):
            return None
        if not self._group_id_of(message):
            return None

        text = str(message.get("processed_plain_text") or "")
        if self._looks_like_command(text) or not self._mention_keyword_hit(text):
            return None
        if not self._bot_referenced(message, text):
            return None

        bot_id = str(message.get("account_id") or "").strip()
        for target, name in self._at_targets(message, bot_id=bot_id):
            # @ 的是官方水表 bot 就跳过，别去查它的绑定（必然回「没绑定」）
            if self._is_foreign_bot_name(name):
                continue
            return target, name
        return None

    def _at_targets(
        self, message: dict[str, Any], bot_id: str = ""
    ) -> list[tuple[str, str]]:
        """消息里 @ 到的其他人 ``[(用户 ID, 显示名)]``；排除 @全体 与机器人自己。"""
        bot_id = bot_id or str(message.get("account_id") or "").strip()
        text = str(message.get("processed_plain_text") or "")
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        raw = message.get("raw_message")
        for seg in raw if isinstance(raw, list) else []:
            if not isinstance(seg, dict) or str(seg.get("type") or "") not in ("at", "mention"):
                continue
            data = seg.get("data")
            data = data if isinstance(data, dict) else {}
            target = str(data.get("target_user_id") or data.get("user_id") or "").strip()
            if not target or target in _MENTION_ALL_TARGETS or target == bot_id or target in seen:
                continue
            seen.add(target)
            name = (
                str(data.get("target_user_cardname") or data.get("target_user_nickname") or "").strip()
                or self._at_display_name(text, target)
                or target
            )
            found.append((target, name))
        return found

    def _bot_referenced(self, message: dict[str, Any], text: str) -> bool:
        """这条消息有没有叫到机器人：@ 了它，或文字里出现它的名字/别名。"""
        cfg = getattr(self.config, "mention_query", None)
        if cfg is not None and not getattr(cfg, "require_bot_ref", True):
            return True
        if self._message_is_at(message):
            return True
        clean = _strip_at_tokens(text).casefold()
        return any(name.casefold() in clean for name in self._bot_names())

    def _bot_names(self) -> set[str]:
        """机器人的名字池：MaiBot 配置里的昵称/别名 + 实际 @ 时学到的群名片。

        昵称与别名读的是 MaiBot 自己的 ``bot_config.toml``（``[bot] nickname`` /
        ``alias_names``），插件配置里不用再填一份，改名字只改一处。
        """
        names = _bot_config_names()
        names.update(self._learned_bot_names)
        return names

    def _strip_bot_names(self, text: str) -> str:
        """把机器人名字从原话里抹掉，否则「@某人 小助手，查他水表」会把它当船名。"""
        for name in sorted(self._bot_names(), key=len, reverse=True):
            if name:
                text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
        return text

    def _learn_bot_name(self, message: dict[str, Any]) -> None:
        """从入站消息里学机器人自己的显示名（``@<机器人昵称:机器人号>`` 或 at 段名片）。"""
        account_id = str(message.get("account_id") or "").strip()
        if not account_id:
            return
        text = str(message.get("processed_plain_text") or "")
        for match in re.finditer(rf"@<([^>:]*):{re.escape(account_id)}>", text):
            name = match.group(1).strip()
            if name:
                self._learned_bot_names.add(name)
        raw = message.get("raw_message")
        for seg in raw if isinstance(raw, list) else []:
            if not isinstance(seg, dict) or str(seg.get("type") or "") not in ("at", "mention"):
                continue
            data = seg.get("data")
            if not isinstance(data, dict):
                continue
            if str(data.get("target_user_id") or "").strip() != account_id:
                continue
            for field in ("target_user_cardname", "target_user_nickname"):
                name = str(data.get(field) or "").strip()
                if name:
                    self._learned_bot_names.add(name)

    def _self_query_target(self, message: dict[str, Any]) -> tuple[str, str] | None:
        """``@机器人 查下我 XX 水表`` 这类自己查自己：返回 (发送者 ID, 显示名)。

        ▍为什么要抢在模型前面
        这条消息原来交给 Maisaka：规划模型只看到工具的 brief 描述，真正写回复的是
        另一个模型，而它**看不到工具结果**。实测它把工具返回的近 90 天真实场次与场均
        说成「23 场、胜率 39%、场均 4.8 万」—— 数字全是编的。
        自己查自己不需要模型发挥，直接查、直接发图，数字就不可能被编。

        带「帮 / 给 / 替 / 他 / 她 / 群友」这类转指就不抢，免得把
        「帮我查下张三的」认成查发送者本人。

        没写「我 / 咱 / 俺」也认：@ 了机器人还点名一条船，默认就是查自己
        （yuyuko 的指令语义也是「不给昵称 = 查自己」）。实测「@机器人 AL信浓水表」
        因为缺「我」被漏到模型，参数被填成 ``ship="信浓"``，查成了普通信浓。
        同理「@机器人  近30天水表」这种只带时间、不带船名的也算自查。
        只认**单个**像船名的词，「@我 张三 蒙大拿水表」这种带别人昵称的仍交给模型。
        """
        if message.get("is_notify") or not self._message_is_at(message):
            return None
        if not self._group_id_of(message):
            return None
        text = str(message.get("processed_plain_text") or "")
        if self._looks_like_command(text) or not self._mention_keyword_hit(text):
            return None
        clean = _strip_at_tokens(text)
        if _OTHER_PERSON_RE.search(clean):
            return None
        if not _SELF_QUERY_RE.search(clean):
            rest = self._strip_bot_names(clean)
            ship = _ship_in_text(rest)
            if ship and len(ship.split()) > 1:
                return None
            # 没点船也没自称：带天数（「近30天水表」）或干脆什么都没说（「水表」）
            # 一样是查自己。实测「@机器人  近30天水表」落到模型后被填成
            # nickname=群名片，工具只回一句「查别人要 server+nickname」，
            # 整条消息什么数据都没出来。
            if not ship and not _days_in_text(rest) and not _is_bare_query(rest):
                return None
        sender = self._sender_id_of(message)
        if not sender:
            return None
        return sender, self._sender_name_of(message) or "你"

    @staticmethod
    def _sender_name_of(message: dict[str, Any]) -> str:
        """取发送者显示名（群名片优先），用于水表前的说明。"""
        info = message.get("message_info")
        info = info if isinstance(info, dict) else {}
        user = info.get("user_info")
        user = user if isinstance(user, dict) else {}
        sender = message.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        for source in (user, sender):
            for field in ("user_cardname", "user_nickname", "cardname", "nickname"):
                value = str(source.get(field) or "").strip()
                if value:
                    return value
        return ""

    @staticmethod
    def _group_id_of(message: dict[str, Any]) -> str:
        info = message.get("message_info")
        info = info if isinstance(info, dict) else {}
        group = info.get("group_info")
        group = group if isinstance(group, dict) else {}
        return str(group.get("group_id") or "").strip()

    def _looks_like_command(self, text: str) -> bool:
        """``/wws`` 或别的斜杠指令一律不抢，留给命令链。

        指令词池里的口语词（水表/战绩…）只有**在唤起配置里启用**时才算指令，
        否则「@某人 水表」会被误判成指令，把 @ 查询整条掐掉。
        """
        stripped = _strip_at_tokens(text)
        if not stripped:
            return False
        match = _WWW_RE.match(stripped)
        if match:
            word = (match.group("word") or "wws").casefold()
            cfg = getattr(self.config, "trigger", None)
            aliases = _parse_wake_aliases(
                str(getattr(cfg, "command_aliases", "wws") or "wws")
            )
            if word in aliases:
                return True
        return stripped[0] in ("/", "!", "！", "／")

    def _mention_keyword_hit(self, text: str) -> bool:
        """消息里是否带了触发词；关掉「需要关键词」时一律算命中。"""
        cfg = getattr(self.config, "mention_query", None)
        if cfg is None or not getattr(cfg, "require_keyword", True):
            return True
        raw = str(getattr(cfg, "keywords", "") or "")
        keywords = [k.strip() for k in re.split(r"[,，、\s]+", raw) if k.strip()]
        if not keywords:
            keywords = list(_DEFAULT_MENTION_KEYWORDS)
        clean = _strip_at_tokens(text)
        if any(word in clean for word in keywords):
            return True
        # 「西西里recent36」「西西里近期95」「查我9个月九月七日」都不带配置关键词，
        # 以前全部落到模型路径（回复模型看不到工具结果，数字靠编）
        return any(pattern.search(clean) for pattern in _EXTRA_QUERY_CUES)

    @staticmethod
    def _at_display_name(text: str, target: str) -> str:
        """从 ``@<昵称:QQ号>`` 里抠显示名（组件没带昵称时兜底）。"""
        match = re.search(rf"@<([^>:]*):{re.escape(target)}>", text)
        return (match.group(1) if match else "").strip()

    def _mention_rate_ok(self, group_id: str) -> bool:
        """按群限流；返回 False 表示这次不查（不吵用户，只记日志）。"""
        cfg = getattr(self.config, "mention_query", None)
        try:
            limit = int(getattr(cfg, "rate_limit", 3))
        except (TypeError, ValueError):
            limit = 3
        now = time.monotonic()
        bucket = self._mention_hits.setdefault(group_id, deque())
        while bucket and now - bucket[0] > _MENTION_WINDOW:
            bucket.popleft()
        if limit > 0 and len(bucket) >= limit:
            logger.info(
                f"[wows] @ 查询超限已跳过: group={group_id} limit={limit}/{_MENTION_WINDOW:.0f}s"
            )
            return False
        bucket.append(now)
        return True

    def _mention_plan(
        self, message: dict[str, Any], target_name: str = ""
    ) -> tuple[str, str, int | None]:
        """按消息原文决定查什么：总表 / 近期 / 单船 / 单船近期。

        ``@ 群友`` 这条路径不进模型，时间与船名只能自己从原文抠：
        「@某人 30天水表」→ recent 30；「@某人 托尔90天水表」→ ship recent 托尔 90。
        船名抠错了上游会回「找不到船」，调用方再退回总表/近期。
        """
        text = self._strip_mention_name(str(message.get("processed_plain_text") or ""), target_name)
        # 「@某人 小助手，查他水表」里的机器人名字不是船名，先抹掉再抠
        text = self._strip_bot_names(text)
        days = _days_in_text(text)
        ship = _ship_in_text(text)
        if ship and days:
            return "ship_recent", ship, days
        if ship:
            return "ship", ship, None
        if days:
            return "recent", "", days
        return "account", "", None

    @staticmethod
    def _strip_mention_name(text: str, name: str) -> str:
        """把被 @ 的人的显示名从原文里去掉，否则会被当成船名。

        ``processed_plain_text`` 里常见「@某群友 彼得罗30天水表」，
        不剥掉前导昵称，船名就变成「某群友 彼得罗」，上游必然查不到。
        """
        name = (name or "").strip()
        # 先把所有 @ 标记整段抹掉：@ 的不一定是被查的人（常见 @机器人 + @群友 混用），
        # 只按 target_name 替换会留下「机器人昵称 射水鱼」这种船名，上游必定找不到
        text = _strip_at_tokens(text)
        if not name:
            return text
        return text.replace(f"@{name}", " ").replace(name, " ")

    def _spawn_mention_query(self, **params: Any) -> None:
        task = asyncio.create_task(self._run_mention_query(**params))
        self._mention_tasks.add(task)
        task.add_done_callback(self._mention_tasks.discard)

    async def _run_mention_query(
        self,
        *,
        stream_id: str,
        group_id: str,
        bot_id: str,
        target_id: str,
        target_name: str,
        kind: str = "account",
        ship: str = "",
        days: int | None = None,
        self_query: bool = False,
        verify_ship: bool = False,
    ) -> None:
        """查被 @ 的人（或发送者自己）绑定的账号，按原文决定口径，发说明 + 水表图。"""
        try:
            if verify_ship and ship and (await ship_name_exists(ship)) is False:
                # 「/哈哈」这种随手发一个斜杠词的：消息已经被 abort 了，
                # 这里再回一句「没找到这艘船」纯属噪音，直接静默丢掉。
                logger.info(f"[wows] 简易查询跳过：{ship!r} 不是上游已知的船名")
                return
            # 绑定关系每次现查：5 分钟缓存会让「刚绑定的人」被判成未绑定，
            # 换了默认账号时说明里的名字也会停在旧号上。这条路径本来就只在 @ 时跑一次。
            identity = await resolve_identity(PLATFORM, target_id, refresh=True)
            server = str((identity or {}).get("server") or "")
            account = str((identity or {}).get("user_name") or "")
            if not server or not account:
                logger.info(f"[wows] @ 查询跳过：{target_id} 未绑定游戏账号（自查={self_query}）")
                if self._mention_llm_unbound():
                    text = await self._mention_unbound_text(
                        stream_id, target_name, self_query=self_query
                    )
                else:
                    text = self._mention_unbound_fallback(target_name, self_query=self_query)
                await reply_text(self.ctx, stream_id, text)
                return

            async with self.runtime.render_lock:
                # 查询一律走 hikari 的 me 模式：init_hikari 的 PlatformId 就是被查人的 QQ，
                # 上游按平台绑定解析到他当前默认绑定的账号。
                # 不再拼「服务器 + 昵称」——昵称里带空格（实测「John Doe」）会被拆成三段，
                # hikari 直接判「未识别的指令」；绑定换了默认账号后，5 分钟身份缓存里的旧昵称
                # 还会把图查到另一个人身上。身份只用来判断有没有绑定、以及发说明时的显示名。
                outcome = await self._run_mention_command(
                    kind=kind,
                    ship=ship,
                    days=days,
                    target_id=target_id,
                    bot_id=bot_id,
                    group_id=group_id,
                    stream_id=stream_id,
                )
                hikari = outcome.hikari
                # 说明里写「实际查到的号」：me 模式按上游当前默认绑定现算，
                # 与绑定列表的缓存口径可能不同，以图里的 userInfo 为准。
                actual_server, actual_account = _observed_identity(hikari)
                if actual_account and (actual_server, actual_account) != (server, account):
                    logger.info(
                        f"[wows] @ 查询账号与绑定快照不一致: 绑定={server}/{account!r} "
                        f"实际={actual_server}/{actual_account!r}"
                    )
                logger.info(
                    f"[wows] @ 查询结果 kind={kind}->{outcome.kind} 天数={days}->{outcome.days} "
                    f"身份={server}/{account!r} 实际={actual_server}/{actual_account!r} "
                    f"status={hikari.Status} "
                    f"server={hikari.Input.Server} 昵称={hikari.Input.AccountName!r} "
                    f"aid={hikari.Input.AccountId}"
                )
                if hikari.Status == "success":
                    note = outcome.note
                    if not note and kind in ("ship", "ship_recent") and outcome.kind != kind:
                        note = f"没查到「{ship}」的数据（可能不在名下、或近期没开过），先给你整账号的~"
                    if note:
                        await reply_text(self.ctx, stream_id, note)
                    await reply_text(
                        self.ctx,
                        stream_id,
                        mention_header(
                            target_name,
                            actual_server or server,
                            actual_account or account,
                            self._mention_scope(outcome.kind, ship, outcome.days),
                            self_query=self_query,
                        ),
                    )
                await self._send_result(stream_id, hikari)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[wows] @ 查询失败 target={target_id}: {e!r}")
            await reply_text(self.ctx, stream_id, user_error_text(classify_error(e)))

    async def _run_mention_command(
        self,
        *,
        kind: str,
        ship: str,
        days: int | None,
        target_id: str,
        bot_id: str,
        group_id: str,
        stream_id: str,
    ) -> _MentionOutcome:
        """按计划查一次；单船近期没数据就如实说明，找不到船才退回账号。

        ▍为什么不再拼「服务器 + 昵称」
        被查人的 QQ 已经作为 PlatformId 传进 hikari，me 模式本身就是「查这个人绑定的
        默认账号」，与绑定列表的 defaultAccount 是同一个口径。拼昵称反而多两个坑：
        昵称含空格会被拆词判成「未识别的指令」；绑定换默认账号后旧昵称会查到别人身上。

        ▍为什么不再拿账号近期顶替单船
        以前「单船 60 天」空窗口会直接退回账号 60 天总表：图是总表、说明却写着
        「近期没开过」，用户看到的就是「明明没记录怎么还出水表」。
        也不再自动放宽到一年：用户要 30 天就只查 30 天，窗口空就如实说没有。
        """

        async def raw_call(command_text: str):
            return await init_hikari(
                command_text=command_text,
                platform=PLATFORM,
                PlatformId=target_id,
                BotId=bot_id,
                GroupId=group_id,
            )

        async def call(command_text: str):
            hikari = await raw_call(command_text)
            return await self._settle_ship_choice(
                hikari, ship, stream_id, rerun=lambda: raw_call(command_text)
            )

        if ship and ship.isdigit():
            # hikari 的 _extract_day_and_date_params 会把指令里所有 ≤3 位纯数字 token
            # 当天数抠掉，「190recent190」抠完没有船名 → 报「缺少船名」→ 旧逻辑退回
            # 整账号近期，用户看到一张账号水表。先换成 U-190 再发指令。
            resolved = await resolve_ship_name(ship)
            if resolved:
                logger.info(f"[wows] 纯数字船名 {ship} 换成 {resolved}")
                ship = resolved

        if kind == "ship_recent" and ship and days:
            hikari = await call(build_ship_recent_command(ship, days))
            if hikari.Status == "success":
                return _MentionOutcome(hikari, kind, days)

            if _window_empty(hikari):
                # 要几天就只查几天：不再放宽到一年。窗口空就是这片窗口里没开过，
                # 拿一年内的旧场次顶上来会让人误以为是最近打的（用户明确要求不放宽）。
                logger.info(f"[wows] @ 单船近期 {ship} 近 {days} 天无记录，如实回，不放宽")
                return _MentionOutcome(
                    hikari.failed(f"「{ship}」最近 {days} 天没有出战记录，没有单船水表可看"),
                    kind,
                    days,
                )

            if _ship_missing(hikari):
                logger.info(f"[wows] @ 单船近期 {ship} 查不到该船数据，直接说明原因")
                return _MentionOutcome(hikari.failed(_ship_missing_text(ship, hikari)), kind, days)

            # 既不空窗口、也不缺船数据（例如指令没解析出船名）时，以前会退回账号近期，
            # 用户要单船却收到一张整账号水表——这正是「190recent190 出了 recent190」。
            # 单船请求一律不拿账号数据顶替，如实说明并让用户换写法。
            detail = str(getattr(getattr(hikari, "Output", None), "Data", "") or "").strip()
            logger.info(
                f"[wows] @ 单船近期未命中（{ship} / {days} 天），不拿账号近期顶替: {detail[:60]}"
            )
            hint = f"（上游：{detail[:60]}）" if detail else ""
            return _MentionOutcome(
                hikari.failed(
                    f"「{ship}」的单船近期没查到数据，没有拿整账号的战绩顶替。"
                    f"换完整船名 / 英文名再试~{hint}"
                ),
                kind,
                days,
            )

        if kind == "ship" and ship:
            hikari = await call(build_ship_command(ship))
            if hikari.Status == "success":
                return _MentionOutcome(hikari, kind)
            if _ship_missing(hikari):
                logger.info(f"[wows] @ 单船未命中（{ship}），直接说明原因，不发总表")
                return _MentionOutcome(hikari.failed(_ship_missing_text(ship, hikari)), kind)
            detail = str(getattr(getattr(hikari, "Output", None), "Data", "") or "").strip()
            logger.info(f"[wows] @ 单船未命中（{ship}），不拿账号总表顶替: {detail[:60]}")
            hint = f"（上游：{detail[:60]}）" if detail else ""
            return _MentionOutcome(
                hikari.failed(
                    f"「{ship}」的生涯数据没查到，没有拿整账号的顶替。"
                    f"换完整船名 / 英文名再试~{hint}"
                ),
                kind,
            )

        if kind == "recent" and days:
            hikari = await call(build_recent_command(days))
            if hikari.Status == "success":
                return _MentionOutcome(hikari, kind, days)
            logger.info(f"[wows] @ 近期未命中（{days} 天），退回账号总表")
            return _MentionOutcome(await call(build_account_command()), "account")

        return _MentionOutcome(await call(build_account_command()), "account")

    async def _settle_ship_choice(self, hikari, ship: str, stream_id: str, rerun=None):
        """把 ``wait``（简称命中多条船）处理掉：唯一候选自动选，有歧义按默认选。

        ``彼得罗`` 会命中彼得罗巴甫洛夫斯克 / 彼得罗扎沃茨克 / 彼得罗·巴格拉季昂，
        ``蒙大拿`` 会命中蒙大拿 + BA蒙大拿。以前 @ 路径把 wait 当失败直接退回总表，
        后来改让用户回序号——群里等 20 秒多半没人回，查询就静默取消。
        现在按候选顺序依次取数，第一个有数据的就发（彼得罗 → 彼得罗巴甫洛夫斯克），
        并在说明里点明用的是哪一条；都没数据才如实说查不到。
        """
        if getattr(hikari, "Status", "") != "wait":
            return hikari
        candidates = hikari.Input.Select_Data or []
        picked = self._pick_candidate(candidates, ship)
        if picked is not None:
            logger.info(f"[wows] @ 单船候选自动选择: {ship!r} -> #{picked}")
            hikari.Input.Select_Index = picked
            return await callback_hikari(hikari)

        logger.info(f"[wows] @ 单船候选有歧义（{ship!r}，{len(candidates)} 条），按顺序找有数据的")
        last = None
        for index in range(1, len(candidates) + 1):
            if index == 1:
                trial = hikari
            elif rerun is not None:
                trial = await rerun()
                if getattr(trial, "Status", "") != "wait":
                    last = trial
                    continue
            else:
                break
            trial.Input.Select_Index = index
            result = await callback_hikari(trial)
            label = str((candidates[index - 1] or {}).get("nameCn") or "").strip()
            if result.Status == "success":
                logger.info(f"[wows] @ 单船候选默认选中: {ship!r} -> #{index} {label}")
                await reply_text(
                    self.ctx,
                    stream_id,
                    f"「{ship}」对上了 {len(candidates)} 条船，先按 {index} {label} 给你看~",
                )
                return result
            logger.info(f"[wows] @ 单船候选 #{index} {label} 无数据：{result.Output.Data!r}")
            last = result
        return last or hikari

    @staticmethod
    def _pick_candidate(candidates: list, ship: str) -> int | None:
        """候选里挑一个：名字完全一致优先，只有一个候选就直接选，否则返回 None 让用户挑。"""
        if not candidates:
            return None
        wanted = (ship or "").strip().lower()
        for index, item in enumerate(candidates, 1):
            if not isinstance(item, dict):
                continue
            names = (
                str(item.get("nameCn") or "").strip().lower(),
                str(item.get("nameEnglish") or "").strip().lower(),
            )
            if wanted and wanted in names:
                return index
        if len(candidates) == 1:
            return 1
        return None

    @staticmethod
    def _mention_scope(kind: str, ship: str, days: int | None) -> str:
        if kind == "ship_recent" and ship and days:
            return f"{ship} · 最近 {days} 天"
        if kind == "ship" and ship:
            return f"{ship} · 生涯"
        if kind == "recent" and days:
            return f"最近 {days} 天"
        return ""

    @staticmethod
    def _mention_unbound_fallback(target_name: str, self_query: bool = False) -> str:
        if self_query:
            return "你还没有绑定窝窝屎账号，查不到自己的战绩哦~"
        return f"「{target_name}」还没有绑定窝窝屎账号，查不到战绩哦~"

    async def _mention_unbound_text(
        self, stream_id: str, target_name: str, self_query: bool = False
    ) -> str:
        """「TA 没绑定」这句话让宿主模型用自己的口吻说，生成失败就用固定文案。

        ▍为什么借用 maisaka.reply.generate
        插件里写死一句「去绑定吧」既不像是麦麦说的，也容易把口吻写僵；
        这个能力复用宿主的人格与回复 Hook，只负责组织一句话，不会自己发出去。
        属于实验性功能，由 ``mention_query.llm_unbound_reply`` 控制，默认关闭。
        """
        fallback = self._mention_unbound_fallback(target_name, self_query=self_query)
        reference = (
            "用户想查自己绑定的战舰世界账号战绩，但 TA 还没有绑定，没有数据可查。"
            "请用一句话顺口告诉对方这件事。"
            if self_query
            else (
                f"有人 @ 了群友「{target_name}」想查 TA 的战绩，"
                f"但「{target_name}」还没有绑定战舰世界账号，没有数据可查。"
                f"请用一句话顺口告诉对方这件事。"
            )
        )
        try:
            result = await self.ctx.maisaka.reply.generate(
                stream_id,
                reference=reference,
                format_instruction="只输出一句话，口语化；不要列表、不要 Markdown、不要教怎么绑定。",
                timeout_ms=20000,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[wows] 未绑定文案生成失败，改用固定文案: {e!r}")
            return fallback
        text = str(result.get("text") or "").strip() if isinstance(result, dict) else ""
        return text or fallback

    # ── LLM 工具（对话式查询） ─────────────────────────────────────
    #
    # 每个工具只做两件事：把参数拼成 hikari 指令、交给 ToolExecutor 跑完。
    # 真正的流程（开关 / 限流 / 查询 / 发图 / 给模型的文字）都在 yuyuko_llm.executor。
    #
    # 返回值统一是 {"success": True, "content": 给模型看的文本}：
    # 业务失败也走 content（里面是行动指引），不让 Host 当成工具故障吞掉内容。

    def _tool_ctx(self, kwargs: dict[str, Any]) -> ToolContext:
        """从 MaiBot 注入的 kwargs 里还原一次工具调用的上下文。

        宿主实际只注入 ``stream_id`` / ``chat_id`` / ``group_id`` / ``platform``；
        群会话的 chat_stream 没有 user_id，所以最后再用消息 Hook 记下的发送者兜底。
        """
        message = kwargs.get("message")
        if not isinstance(message, dict):
            message = {}
        sender = message.get("sender")
        if not isinstance(sender, dict):
            sender = {}
        if not self._tool_ctx_logged:
            # 上线后第一次调用记一次，便于核对 Host 到底注入了哪些字段
            self._tool_ctx_logged = True
            logger.info(f"[wows] Tool 注入字段: {sorted(kwargs)}")
        stream_id = str(kwargs.get("stream_id") or "")
        group_id = str(kwargs.get("group_id") or message.get("group_id") or "")
        user_id = str(
            kwargs.get("user_id")
            or message.get("user_id")
            or message.get("sender_id")
            or sender.get("user_id")
            or ""
        ).strip()
        if not user_id:
            # 群会话的 chat_stream 没有 user_id（宿主只给群号），
            # 用消息 Hook 记下的「该会话最近发送者」兜底，否则「查我的」永远查不了。
            user_id = self._recent_sender(stream_id, group_id)
        return ToolContext(
            ctx=self.ctx,
            stream_id=stream_id,
            user_id=user_id,
            group_id=group_id,
            bot_id=str(message.get("account_id") or ""),
            platform=PLATFORM,
        )

    def _recent_sender(self, stream_id: str, group_id: str) -> str:
        """按会话/群回查最近一条消息的发送者（短 TTL，避免拿别的群友顶替）。"""
        # 存的时候 platform 统一小写（见 _at_state_keys），这里必须一致，否则永远查不到
        platform = PLATFORM.lower()
        now = time.monotonic()
        keys = [(platform, f"s:{stream_id}")] if stream_id else []
        if group_id:
            keys.append((platform, f"g:{group_id}"))
        # 先认「最近一条查询消息」：规划模型慢的时候工具要等两分钟以上，
        # 短 TTL 的「最近发送者」早过期了，查「我的」就会变成「没绑定」。
        for key in keys:
            query = self._pending_query.get(key)
            if query is None or not query[0] or now - query[2] > _PENDING_QUERY_TTL:
                continue
            logger.info(
                f"[wows] 发送者按最近的查询消息回查: {key} -> {query[0]} 原文={query[1][:30]!r}"
            )
            return query[0]
        for key in keys:
            record = self._at_state.get(key)
            if record is None or not record[1] or now - record[2] > _SENDER_STATE_TTL:
                continue
            return record[1]
        return ""

    def _recent_text(self, stream_id: str, group_id: str, user_id: str) -> str:
        """回查这位用户最近一条消息的原文；只认发送者本人，避免拿群友的话顶替。"""
        platform = PLATFORM.lower()
        keys: list[tuple[str, str]] = []
        if stream_id:
            keys.append((platform, f"s:{stream_id}"))
        if user_id:
            keys.append((platform, f"u:{user_id}"))
        if group_id:
            keys.append((platform, f"g:{group_id}"))
        now = time.monotonic()
        best: tuple[str, str, float] | None = None
        for key in keys:
            record = self._sent_text.get(key)
            if record is None or now - record[2] > _SENT_TEXT_TTL:
                continue
            sender, _text, _ts = record
            if user_id and sender and sender != user_id:
                continue
            if best is None or record[2] > best[2]:
                best = record
        return best[1] if best else ""

    def _resolve_days(self, days: int | None, kwargs: dict[str, Any]) -> int | None:
        """天数口径：用户原话里说清了就按原话，模型填的只作兜底。

        ▍为什么要盖过模型的 days
        规划模型经常把「西西里90天水表」里的 90 丢掉，工具拿默认 7 天去查，
        回复却说「90 天就 7 场」—— 数据对不上用户的问题。工具上下文拿不到触发消息，
        所以借 Hook 记下的原文把天数补回来。
        """
        try:
            ctx = self._tool_ctx(kwargs)
            text = self._recent_text(ctx.stream_id, ctx.group_id, ctx.user_id)
        except Exception:  # noqa: BLE001
            return _clamp_days(days)
        parsed = _days_in_text(text)
        if parsed and parsed != _clamp_days(days):
            logger.info(f"[wows] 天数按消息原文纠正: 模型={days} 原文={parsed} 消息={text[:40]!r}")
        return parsed or _clamp_days(days)

    def _resolve_ship(self, ship: str, kwargs: dict[str, Any]) -> str:
        """船名口径：原话里带了换皮标记（AL信浓 / 北卡CLR）就按原话。

        ▍为什么盖过模型的 ship
        实测用户发「AL 信浓水表」，没 @ 机器人，走模型路径后参数被填成
        ``ship="信浓"``，前缀被吃掉，查到的是普通信浓（yuyuko 对「信浓」返回
        AL信浓 / 信浓 两个候选）。工具拿不到触发消息，只能借 Hook 记下的原文补回来；
        只在「模型给的船名包含于原话船名」时才纠正，避免把闲聊词当船名。
        """
        model_ship = (ship or "").strip()
        try:
            ctx = self._tool_ctx(kwargs)
            text = self._recent_text(ctx.stream_id, ctx.group_id, ctx.user_id)
        except Exception:  # noqa: BLE001
            return model_ship
        parsed = _ship_in_text(text) if text else ""
        if not parsed or re.search(r"[和与及、,，]", parsed):
            return model_ship
        if model_ship:
            if model_ship.casefold() not in parsed.casefold():
                return model_ship
            if len(parsed) - len(model_ship) > 8:
                return model_ship
        if parsed.casefold() != model_ship.casefold():
            logger.info(
                f"[wows] 船名按消息原文纠正: 模型={model_ship!r} 原文={parsed!r} 消息={text[:40]!r}"
            )
        return parsed

    def _text_days(self, kwargs: dict[str, Any]) -> tuple[bool, int | None]:
        """回查触发消息原文，返回 (是否拿到原文, 原文里的天数)。

        拿不到原文时返回 (False, None)，调用方应保留模型给的天数；
        拿到原文但抠不出时间，说明用户确实没提时间，模型填的天数是脑补。
        """
        try:
            ctx = self._tool_ctx(kwargs)
            text = self._recent_text(ctx.stream_id, ctx.group_id, ctx.user_id)
        except Exception:  # noqa: BLE001
            return False, None
        if not text:
            return False, None
        return True, _days_in_text(text)

    def _original_text(self, kwargs: dict[str, Any]) -> str:
        """回查触发消息原文；拿不到返回空串（调用方按「无从判断」处理）。"""
        try:
            ctx = self._tool_ctx(kwargs)
            return self._recent_text(ctx.stream_id, ctx.group_id, ctx.user_id)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _with_window(
        payload: dict[str, Any], days: int | None, note: str = ""
    ) -> dict[str, Any]:
        """在成功数据前标出统计窗口，避免模型把 7 天说成 90 天。"""
        content = str(payload.get("content") or "")
        if not days or not content.startswith("以下是"):
            return payload
        label = note or f"最近 {days} 天"
        return {**payload, "content": f"【统计窗口】{label}。\n{content}"}

    async def _run_tool(
        self,
        kwargs: dict[str, Any],
        command_text: str,
        extractor: str,
        tool_name: str,
        **options: Any,
    ) -> dict[str, Any]:
        """跑一次工具调用；异常按网络/程序两类分别记日志并给出对应话术。"""
        stream_id = str(kwargs.get("stream_id") or "")
        if options.get("select_index") is not None:
            # 没有候选列表时模型也会填序号；带着它走二次选择分支会报「找不到船」
            seen = self._pending_select.get(stream_id)
            if seen is None or time.monotonic() - seen > _SELECT_STATE_TTL:
                logger.info(f"[wows] 忽略无候选的 select_index={options['select_index']}")
                options["select_index"] = None
            else:
                self._pending_select.pop(stream_id, None)
        blocked = self._at_gate(kwargs, options.get("select_index"))
        if not blocked:
            blocked = self._query_intent_gate(kwargs, options.get("select_index"))
        if blocked:
            return {"success": True, "content": blocked}
        try:
            text = await self.tools.execute(
                self._tool_ctx(kwargs), command_text, extractor, tool_name, **options
            )
        except Exception as e:  # noqa: BLE001
            kind = classify_error(e)
            message = f"[wows] LLM 工具 {tool_name} 执行异常（{kind}）: {e!r}"
            if kind == ERROR_EXCEPTION:
                logger.error(message, exc_info=True)
            else:
                logger.warning(message)
            text = user_error_text(kind)
        if stream_id and text.startswith("这一步需要用户选择"):
            self._pending_select[stream_id] = time.monotonic()
        return {"success": True, "content": text}

    def _text_says_self(self, kwargs: dict[str, Any]) -> bool:
        """用户原话是不是在查自己（「我」「自己」）。"""
        try:
            ctx = self._tool_ctx(kwargs)
            text = self._recent_text(ctx.stream_id, ctx.group_id, ctx.user_id)
        except Exception:  # noqa: BLE001
            return False
        return "我" in text or "自己" in text

    def _identity_for(
        self, server: str | None, nickname: str | None, kwargs: dict[str, Any]
    ) -> tuple[str, str]:
        """统一身份口径：原话在查自己时，忽略模型塞的昵称/服务器。

        ▍为什么必须拦
        实测模型会把机器人自己的名字（小助手）当昵称填进来，命令变成
        ``小助手 ship recent 托尔 90``，hikari 直接判「未查询到绑定账号」。
        用户原话里带「我」就是查自己，此时留空让插件注入绑定身份才是对的。
        """
        server, nickname = _identity(server, nickname)
        if nickname and self._text_says_self(kwargs):
            logger.info(f"[wows] 原话在查自己，忽略模型给的 nickname={nickname!r}")
            return "", ""
        return server, nickname

    def _tool_text(self, kwargs: dict[str, Any]) -> str:
        """回查用户原话（工具上下文里没有消息本体，只能借 Hook 记下的文本）。"""
        try:
            ctx = self._tool_ctx(kwargs)
            return self._recent_text(ctx.stream_id, ctx.group_id, ctx.user_id)
        except Exception:  # noqa: BLE001
            return ""

    def _text_at_other(self, text: str, kwargs: dict[str, Any]) -> bool:
        """原话里有没有 @ 到机器人以外的人（有的话昵称就不能丢）。"""
        try:
            bot_id = self._tool_ctx(kwargs).bot_id
        except Exception:  # noqa: BLE001
            bot_id = ""
        names = self._bot_names()
        for match in re.finditer(r"@<([^>:]*):(\d+)>", text or ""):
            name, uid = match.group(1).strip(), match.group(2)
            if uid == bot_id or name in names:
                continue
            return True
        return False

    def _nickname_invented(self, nickname: str, kwargs: dict[str, Any]) -> bool:
        """模型给的昵称原话里根本没有——多半是把 QQ 群名片当成了游戏昵称。

        实测「@机器人  近30天水表」被填成 ``nickname="某群友"``、
        「看看我的水表」被填成 ``nickname="另一位群友"``，都不是游戏昵称。
        只给昵称不给服务器本来就查不了，与其反问「你哪个服的」，不如按查自己走。
        """
        text = self._tool_text(kwargs)
        if not text or not nickname or nickname in text:
            return False
        return not self._text_at_other(text, kwargs)

    def _identity_or_hint(
        self, server: str | None, nickname: str | None, kwargs: dict[str, Any]
    ) -> tuple[str, str, str | None]:
        """同上，但「只给昵称不给服务器」时直接给出可照做的下一步。

        hikari 只认「服务器+昵称」，单给昵称会被当成服务器名，报「未识别的指令」，
        模型看不懂就会拿同样的参数反复重试；这里先拦住。
        昵称压根不在原话里（群名片）时不算「查别人」，清空后按查自己继续。
        """
        server, nickname = self._identity_for(server, nickname, kwargs)
        if nickname and not server:
            if self._nickname_invented(nickname, kwargs):
                logger.info(f"[wows] 模型给的 nickname={nickname!r} 不在原话里，按查自己处理")
                return "", "", None
            return server, nickname, (
                "查别人的战绩要同时给 server（亚服/国服/欧服/美服/俄服）和 nickname；"
                "如果查的是当前用户本人，把 server 和 nickname 都留空再调用一次。"
            )
        return server, nickname, None

    @Tool(
        "wws_account",
        brief_description=(
            "整账号生涯总表：不分船、不分时间；带船名或时间范围时别用这个"
        ),
        detailed_description=(
            "查询玩家的总场次、胜率、场均伤害、PR 等总体数据，并包含各战斗模式"
            "（随机 / 单野 / 自行车 / 三轮车 / 排位）与逐舰种、逐等级的场次分布。"
            "用户问「我的战绩怎么样」「查一下 XX 的水表」「我玩得如何」时调用。"
            "注意：总表是历史累计数据，没有按天 / 按月的维度。用户提到"
            "「今天 / 昨天 / 这周 / 这个月 / 最近」时不要用本工具，改用 wws_recent。"
            "拿到数据后请用自然语言点评玩家的水平和风格。"
            "server 与 nickname 都不填 = 查当前用户自己。"
        ),
        parameters=[
            _p_server(),
            _p_nickname(),
        ],
        core_tool=True,
    )
    async def tool_wws_account(
        self, server: str = "", nickname: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        server, nickname, hint = self._identity_or_hint(server, nickname, kwargs)
        if hint:
            return {"success": True, "content": hint}
        return await self._run_tool(
            kwargs,
            build_account_command(server, nickname),
            "account",
            "wws_account",
            inject_identity=not nickname,
        )

    @Tool(
        "wws_ship",
        brief_description=(
            "单船生涯战绩（不分时间）；「X水表」没提时间就用它，提了时间改用 wws_ship_recent；"
            "ship 照抄用户原话，AL信浓 / 北卡CLR 这类换皮标记一个都不能漏"
        ),
        detailed_description=(
            "查询玩家在指定战舰上的**历史累计**表现（生涯场次、胜率、伤害等）。"
            "用户问「我的俾斯麦打得怎么样」「XX 这条船战绩如何」时调用。"
            "⚠ 本工具没有时间维度，不能回答「最近/今天/这周/这一个月/两个月」："
            "句子里只要出现时间范围或具体天数（今天、这周、30 天、两个月、一年），"
            "就必须改用 wws_ship_recent，否则会拿生涯数据冒充近期战绩。"
            "ship 必填，中英文船名均可（如 俾斯麦 / Bismarck），支持简写与别名"
            "（北卡 / 阿达尔 / 哈巴 / 彼得罗 / z52 都能落到原版船）。"
            "换皮船（克莱贝尔CLR、北卡CLR、AL信浓……）必须带换皮标记，"
            "核心名可以简写，但不能漏掉标记；只写原船名会查原版。"
            "挑不出唯一目标时工具会返回带序号的候选列表，"
            "请让用户选，然后带 select_index 重新调用。"
        ),
        parameters=[
            _p_ship(required=True),
            _p_server(),
            _p_nickname(),
            _p_select(),
        ],
        core_tool=True,
    )
    async def tool_wws_ship(
        self,
        ship: str = "",
        server: str = "",
        nickname: str = "",
        select_index: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        server, nickname, hint = self._identity_or_hint(server, nickname, kwargs)
        if hint:
            return {"success": True, "content": hint}
        if select_index is None:
            ship = self._resolve_ship(ship, kwargs)
        if ship.isdigit():
            # hikari 会把 ≤3 位纯数字 token 当天数抠掉（190 → U-190 必须先换名）
            ship = await resolve_ship_name(ship) or ship
        return await self._run_tool(
            kwargs,
            build_ship_command(ship, server, nickname),
            "ship",
            "wws_ship",
            inject_identity=not nickname,
            prefer_exact=ship,
            select_index=select_index,
        )

    @Tool(
        "wws_recent",
        brief_description=(
            "整账号近期汇总（所有船混算）；出现具体船名时改用 wws_ship_recent；"
            "每次都要实际调用，不能凭记忆复述上一轮结果"
        ),
        detailed_description=(
            "最常用的近期查询：返回指定天数内各战斗模式的统计与逐船明细。"
            "用户问「我最近打得怎么样」「今天战绩如何」「这周水表」时调用。"
            "凡是带时间范围的账号级查询（今天 / 昨天 / 这周 / 这个月 / 两个月）都用本工具，"
            "不要用 wws_account —— 总表没有按天统计。"
            "days 必须按用户说法显式填（省掉只有 7 天）：今天=1、昨天=2、这周=7、"
            "这一个月=30、两个月=60、三个月=90、半年=180、一年=360（最大 366）。"
            "如果用户同时点了具体船名（「两个月托尔水表」），改用 wws_ship_recent。"
            "battle_mode 只在用户明确指定模式时填写，可选：随机 / 单野 / 自行车 / 三轮车 / 排位；"
            "不填则返回全部模式，建议不填，由你根据数据自行判断该点评哪个。"
        ),
        parameters=[
            _p_days(),
            _p_battle_mode(),
            _p_server(),
            _p_nickname(),
        ],
        core_tool=True,
    )
    async def tool_wws_recent(
        self,
        days: int | None = None,
        battle_mode: str = "",
        server: str = "",
        nickname: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        server, nickname, hint = self._identity_or_hint(server, nickname, kwargs)
        if hint:
            return {"success": True, "content": hint}
        days = self._resolve_days(days, kwargs)
        payload = await self._run_tool(
            kwargs,
            build_recent_command(_clamp_days(days), server, nickname),
            "recent",
            "wws_recent",
            battle_mode=battle_mode or None,
            inject_identity=not nickname,
        )
        return self._with_window(payload, days)

    @Tool(
        "wws_recent_random",
        brief_description="整账号近期随机战；单船改用 wws_ship_recent",
        detailed_description=(
            "只统计随机战 PVP。用户明确说「最近随机战」「随机打得怎么样」时调用。"
            "用户只是泛泛地说「最近战绩」时，应优先用 wws_recent。"
            "days 为最近天数（默认 7，最大 366=一年）。"
        ),
        parameters=[_p_days(), _p_server(), _p_nickname()],
    )
    async def tool_wws_recent_random(
        self,
        days: int | None = None,
        server: str = "",
        nickname: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        server, nickname, hint = self._identity_or_hint(server, nickname, kwargs)
        if hint:
            return {"success": True, "content": hint}
        days = self._resolve_days(days, kwargs)
        payload = await self._run_tool(
            kwargs,
            build_recent_command(
                _clamp_days(days), server, nickname, mode=COMMAND_BUILDERS["recent_random"]
            ),
            "recent",
            "wws_recent_random",
            inject_identity=not nickname,
        )
        return self._with_window(payload, days)

    @Tool(
        "wws_recent_rank",
        brief_description="整账号近期排位战；单船改用 wws_ship_recent",
        detailed_description=(
            "只统计排位战。用户说「最近排位」「排位打得怎么样」时调用。"
            "days 为最近天数（默认 7，最大 366=一年）。"
        ),
        parameters=[_p_days(), _p_server(), _p_nickname()],
    )
    async def tool_wws_recent_rank(
        self,
        days: int | None = None,
        server: str = "",
        nickname: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        server, nickname, hint = self._identity_or_hint(server, nickname, kwargs)
        if hint:
            return {"success": True, "content": hint}
        days = self._resolve_days(days, kwargs)
        payload = await self._run_tool(
            kwargs,
            build_recent_command(
                _clamp_days(days), server, nickname, mode=COMMAND_BUILDERS["recent_rank"]
            ),
            "recent",
            "wws_recent_rank",
            inject_identity=not nickname,
        )
        return self._with_window(payload, days)

    @Tool(
        "wws_ship_recent",
        brief_description=(
            "单船+天数的水表，只在句中有船名且明确时间时用（例：西西里90天水表）；没提时间不要用；"
            "ship 照抄用户原话，AL信浓 / 北卡CLR 这类换皮标记一个都不能漏；"
            "每次都要实际调用，不能凭记忆复述上一轮结果"
        ),
        detailed_description=(
            "单船 + 时间段，**唯一**能回答「某条船最近 N 天打得怎么样」的工具。"
            "句子里同时出现船名和时间就必须用它："
            "「我最近开俾斯麦怎么样」「两个月托尔水表」「西西里 90 天战绩」「昨天这船打得如何」。"
            "不要用 wws_ship —— 那是生涯累计，没有时间维度。"
            "days 必须按用户说法显式填（省掉只有 7 天）：今天=1、昨天=2、这周=7、"
            "这一个月=30、两个月=60、三个月=90、半年=180、一年=360（最大 366）。"
            "ship 必填；用户没提船名时应改用 wws_recent。"
            "船名支持简写与别名（北卡 / 阿达尔 / 哈巴 / 彼得罗 / z52 / 英文名），"
            "插件会自动落到原版船；换皮船要带换皮标记（克莱贝尔CLR、北卡CLR、AL信浓……），"
            "核心名可以简写，但标记不能省。"
            "返回候选列表时让用户选，然后带 select_index 重新调用。"
        ),
        parameters=[_p_ship(required=True), _p_days(), _p_server(), _p_nickname(), _p_select()],
        core_tool=True,
    )
    async def tool_wws_ship_recent(
        self,
        ship: str = "",
        days: int | None = None,
        server: str = "",
        nickname: str = "",
        select_index: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        server, nickname, hint = self._identity_or_hint(server, nickname, kwargs)
        if hint:
            return {"success": True, "content": hint}
        if select_index is None:
            ship = self._resolve_ship(ship, kwargs)
        if ship.isdigit():
            # hikari 会把 ≤3 位纯数字 token 当天数抠掉（190 → U-190 必须先换名）
            ship = await resolve_ship_name(ship) or ship
        days = self._resolve_days(days, kwargs)
        days_used = _clamp_days(days)
        if days_used:
            # 模型会从对话历史里脑补天数（实测用户说「拉蒂格水表」，它填了 90）。
            # 原文拿得到且抠不出时间，就以原文为准，按「没提时间」处理。
            text_known, text_days = self._text_days(kwargs)
            if text_known and text_days is None:
                logger.info(
                    f"[wows] 单船 {ship!r} 原话没提时间，忽略模型填的 {days_used} 天，改查生涯"
                )
                days_used = None
        if not days_used:
            # 用户只说「拉蒂格水表」时模型也会挑这个工具；不带天数走 recent 会落到
            # hikari 的默认窗口（实测只查到当天，再放宽到一年就变成「近期水表」），
            # 用户要的其实是生涯总表。这里直接改查生涯，并在内容里说明。
            logger.info(f"[wows] 单船 {ship!r} 没给时间范围，改为查生涯总表")
            payload = await self._run_tool(
                kwargs,
                build_ship_command(ship, server, nickname),
                "ship",
                "wws_ship_recent",
                inject_identity=not nickname,
                prefer_exact=ship,
                select_index=select_index,
            )
            content = str(payload.get("content") or "")
            if content.startswith("以下是"):
                payload = {
                    **payload,
                    "content": (
                        "用户没有指定时间范围，这里查的是这艘船的生涯总表（不分时间）。\n" + content
                    ),
                }
            return payload
        payload = await self._run_tool(
            kwargs,
            build_ship_recent_command(ship, days_used, server, nickname),
            "ship_recent",
            "wws_ship_recent",
            inject_identity=not nickname,
            prefer_exact=ship,
            select_index=select_index,
        )

        # 窗口内没战绩就如实说，不放大到一年：用户要几天就只查几天，
        # 拿一年里的旧场次顶上来会让人误以为是最近打的（用户明确要求不放宽）。
        # 注意 `_run_tool` 永远返回 success=True（业务失败也走 content）。
        return self._with_window(payload, days)

    @Tool(
        "wws_recent_battles",
        brief_description="查询玩家逐场的近期战斗明细",
        detailed_description=(
            "返回每一场的时间、结果、伤害等明细，而不是汇总统计。"
            "用户问「我最近这场打得怎么样」「刚才那把如何」「逐场看看」时调用。"
            "用户只想看总体近况时用 wws_recent 更合适。"
        ),
        parameters=[_p_server(), _p_nickname()],
    )
    async def tool_wws_recent_battles(
        self, server: str = "", nickname: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        server, nickname, hint = self._identity_or_hint(server, nickname, kwargs)
        if hint:
            return {"success": True, "content": hint}
        return await self._run_tool(
            kwargs,
            build_literal_command("recents", server, nickname),
            "recents",
            "wws_recent_battles",
            inject_identity=not nickname,
        )

    @Tool(
        "wws_ships",
        brief_description="按等级/国家/舰种筛选玩家战舰战绩列表",
        detailed_description=(
            "可筛等级区间、国家、舰种。用户问「我 10 级战列舰都打得怎么样」"
            "「日系船的战绩」「8 到 10 级的巡洋舰」时调用。"
            "返回列表可能较长，请自行归纳要点，不要把整张表念给用户。"
            "min_level 最低等级（1-11，不填为 5）；max_level 最高等级（不填为不限）；"
            "nation 如 日本 / 美国 / 苏联 / 德国 / 英国 / 法国 / 意大利 / 泛亚 / 欧洲 / 荷兰 / 西班牙 / 泛美；"
            "ship_type 如 战列舰 / 巡洋舰 / 驱逐舰 / 航空母舰 / 潜艇。"
        ),
        parameters=[
            _p_int("min_level", "最低等级（1-11），不填为 5"),
            _p_int("max_level", "最高等级（1-11），不填为不限"),
            _p_str("nation", "国家/系别，如 日本 / 美国 / 苏联 / 德国 / 英国 / 法国 / 意大利 / 泛亚 / 欧洲 / 荷兰 / 西班牙 / 泛美"),
            _p_str("ship_type", "舰种，如 战列舰 / 巡洋舰 / 驱逐舰 / 航空母舰 / 潜艇"),
            _p_server(),
            _p_nickname(),
        ],
    )
    async def tool_wws_ships(
        self,
        min_level: int | None = None,
        max_level: int | None = None,
        nation: str = "",
        ship_type: str = "",
        server: str = "",
        nickname: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        server, nickname, hint = self._identity_or_hint(server, nickname, kwargs)
        if hint:
            return {"success": True, "content": hint}
        return await self._run_tool(
            kwargs,
            build_ships_command(min_level, max_level, nation or None, ship_type or None, server, nickname),
            "ships",
            "wws_ships",
            inject_identity=not nickname,
        )

    @Tool(
        "wws_roll_ship",
        brief_description="从用户已拥有的战舰里随机抽一条（娱乐功能）",
        detailed_description=(
            "当用户说「随机来条船」「抽个船玩玩」「今天开什么船」时调用。"
            "随机范围是当前用户**已绑定账号里拥有的船**，所以需要用户先绑定；"
            "未绑定时会返回引导，此时请让用户先绑定再试。"
            "可按国家、舰种、等级缩小范围，都不填就是从 TA 拥有的全部战舰里随机。"
        ),
        parameters=[
            _p_str("nation", "限定国家/系别，如 日本 / 美国 / 苏联 / 德国 / 英国"),
            _p_str("ship_type", "限定舰种，如 战列舰 / 巡洋舰 / 驱逐舰 / 航空母舰 / 潜艇"),
            _p_int("level", "限定等级（1-11）"),
        ],
    )
    async def tool_wws_roll_ship(
        self,
        nation: str = "",
        ship_type: str = "",
        level: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await self._run_tool(
            kwargs,
            build_roll_command(nation or None, ship_type or None, level),
            "roll",
            "wws_roll_ship",
            require_user=True,
        )

    @Tool(
        "wws_bind",
        brief_description="把游戏账号绑定到当前用户",
        detailed_description=(
            "绑定后该用户说「查我的战绩」时无需再提供昵称。以下情况应主动引导绑定："
            "1) 用户要查自己的战绩但尚未绑定（查询返回未绑定提示时）；"
            "2) 用户直接说出了自己的游戏昵称，希望以后都能查；"
            "3) 用户问「怎么绑定」「为什么查不了我的」。"
            "引导时先问清楚昵称和服务器，别让用户自己猜参数格式。"
            "nickname 与 server 都必填，server 可选：亚服 / 国服 / 欧服 / 美服 / 俄服。"
            "注意：如果 wws_bind_list 显示该用户已经绑定过账号，不要重复绑定——"
            "查 TA 自己的战绩直接调用 wws_account / wws_recent 并留空 server 和 nickname。"
            "nickname 必须是游戏内昵称，不能用 QQ 昵称或群名片。"
        ),
        parameters=[
            _p_nickname(required=True, description="用户的游戏昵称，必须提供（与游戏内完全一致）"),
            _p_server(required=True, description="账号所在服务器，必须提供：亚服 / 国服 / 欧服 / 美服 / 俄服"),
        ],
        core_tool=True,
    )
    async def tool_wws_bind(
        self, nickname: str = "", server: str = "", **kwargs: Any
    ) -> dict[str, Any]:
        server, nickname = _identity(server, nickname)
        text = self._original_text(kwargs)
        if text and not _BIND_INTENT_RE.search(_strip_at_tokens(text)):
            logger.info(
                f"[wows] 原话没要求绑定，拒绝模型调用 wws_bind: 昵称={nickname!r} 消息={text[:40]!r}"
            )
            return {
                "success": True,
                "content": (
                    "用户这条消息没有要求绑定账号，不要调用 wws_bind，也不要把聊天里出现的昵称写进绑定。"
                    "只是查自己的战绩就直接调用查询工具；确实要绑定时，让用户明确说「绑定」"
                    "并给出服务器和游戏内昵称。"
                ),
            }
        command = await _bind_by_aid(server, nickname) or build_bind_command(server, nickname)
        return await self._run_tool(kwargs, command, "bind", "wws_bind")

    @Tool(
        "wws_bind_list",
        brief_description="查看当前用户已绑定的游戏账号",
        detailed_description=(
            "当用户问「我绑定了什么」、绑定失败怀疑绑错、或查询结果与预期不符时调用。"
        ),
        parameters=[],
    )
    async def tool_wws_bind_list(self, **kwargs: Any) -> dict[str, Any]:
        return await self._run_tool(
            kwargs, COMMAND_BUILDERS["bind_list"], "bind", "wws_bind_list"
        )

    @Tool(
        "wws_bind_change",
        brief_description="在已绑定的多个游戏账号之间切换",
        detailed_description=(
            "需要用户已绑定两个以上账号。用法分两步：先不带 select_index 调用本工具"
            "（会返回绑定列表），把列表转述给用户；用户选定后再带 select_index 调用一次完成切换。"
        ),
        parameters=[_p_int("select_index", "要切换到的序号（从 1 开始）。不填则只返回绑定列表供用户选择")],
    )
    async def tool_wws_bind_change(
        self, select_index: int | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        if select_index is None:
            # 第一步：先给绑定列表（change_bind 不带序号时会返回候选项，不够直观）
            return await self._run_tool(
                kwargs, COMMAND_BUILDERS["bind_list"], "bind", "wws_bind_list"
            )
        return await self._run_tool(
            kwargs,
            COMMAND_BUILDERS["bind_change"],
            "bind",
            "wws_bind_change",
            select_index=select_index,
        )

    def _token_configured(self) -> bool:
        """是否已填写 yuyuko API Token（空串/纯空白视为未配置）。"""
        return bool((self.config.wows.wows_token or "").strip())

    @staticmethod
    def _is_token_free(rest: str) -> bool:
        """该指令是否无需 token（帮助页）。"""
        parts = rest.split(maxsplit=1)
        return (parts[0].lower() if parts else "") in _TOKEN_FREE_COMMANDS


def create_plugin() -> MaiBotPlugin:
    """MaiBot 插件工厂函数。"""
    return WowsYuyukoPlugin()
