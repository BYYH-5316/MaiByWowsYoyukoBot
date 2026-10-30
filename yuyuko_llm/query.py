"""LLM 工具的查询编排层。

把「hikari-core 查询 -> 提取精简字段 -> 渲染发图」串起来，并负责：
把工具参数拼成 hikari 认识的指令文本、判断该不该渲染、格式化候选列表。

▍为什么用 init_hikari_no_output 而不是 init_hikari
``init_hikari`` = ``init_hikari_no_output`` + ``output_hikari``，而 ``output_hikari``
会把 ``Output.Data`` **就地替换**成 HTML 字符串、再替换成图片 bytes。
图片对语言模型毫无价值，而原始 dict 才是提取字段的输入 ——
所以这里先走 ``no_output`` 拿数据，提取完再单独调用 ``output_hikari`` 渲染给用户看。
一份数据两用，且不需要改 hikari-core。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from hikari_core import Hikari_Model, hikari_config, init_hikari_no_output, output_hikari

from ..wows_bot.log import logger

from .extract import BattleStatsExtractor, normalize_battle_mode
from .guide import classify_error

__all__ = [
    "QueryRunner",
    "QueryResult",
    "build_account_command",
    "build_ship_command",
    "build_recent_command",
    "build_ships_command",
    "build_bind_command",
    "build_roll_command",
    "resolve_account_id",
]


# ===========================================================================
# 「查自己」的身份解析
#
# ▍为什么要插件自己解析，而不是让 hikari 的 me 模式去查
# hikari 的部分功能在「查自己」时把 **平台侧用户 ID 直接当成游戏 accountId**
# 去请求（例如 account/ships.py 的 `server = Input.Platform; accountId = Input.PlatformId`），
# 于是请求变成 `?server=QQ&accountId=<QQ号>`，上游查不到该账号，返回空数据。
#
# 那是 vendored 上游代码，本插件不改它（改了下次同步即被覆盖）。
# 取而代之：插件先用绑定接口解析出「该平台用户绑定的游戏账号」，
# 再把 `服务器 + 昵称` 拼进指令，让 hikari 走「服务器+昵称」那条正常路径。
# ===========================================================================

# 平台适配器名 -> yuyuko 侧的平台类型取值
_PLATFORM_TYPE = {
    "QQ": "QQ",
    "QQ_OFFICIAL": "QQ_CHANNEL",
    "QQ_CHANNEL": "QQ_CHANNEL",
}

_IDENTITY_CACHE: dict[tuple[str, str], dict[str, str]] = {}
_IDENTITY_TTL = 300.0
_identity_cached_at: dict[tuple[str, str], float] = {}


async def resolve_identity(
    platform: str, platform_id: str, refresh: bool = False
) -> dict[str, str] | None:
    """解析平台用户当前绑定的游戏账号。

    Args:
        refresh: 跳过缓存强制回源（@ 查询用：用户刚换绑定时缓存会给出旧账号）。

    Returns:
        ``{"account_id", "user_name", "server"}``；未绑定或查询失败返回 ``None``。
        带 5 分钟缓存（同一账号在一轮对话里会被反复查询）。

    ▍为什么借用 hikari 的 client
    yuyuko 的接口对请求头有要求（除 Authorization 还要 ``Yuyuko-Client-Type``，
    缺了会回 409「请求头不符合标准」）。自己拼 httpx 容易漏，
    直接用 ``get_client_yuyuko`` 与上游保持一致，也复用它的连接池。
    """
    key = (platform, str(platform_id))
    import time

    cached = _IDENTITY_CACHE.get(key) if not refresh else None
    if cached and (time.monotonic() - _identity_cached_at.get(key, 0.0)) < _IDENTITY_TTL:
        return cached

    platform_type = _PLATFORM_TYPE.get(platform, platform)
    url = f"{hikari_config.yuyuko_url}/api/user/platform/bind/list"
    params = {"platformType": platform_type, "platformId": str(platform_id)}
    try:
        from hikari_core.core.http_client import get_client_yuyuko
        from hikari_core.core.model import Hikari_Model

        client = await get_client_yuyuko(Hikari_Model().UserInfo)
        resp = await client.get(url, params=params, timeout=20)
        result = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"解析绑定账号失败: {e!r}")
        return None

    if result.get("code") != 200 or not result.get("data"):
        logger.info(f"平台用户未绑定游戏账号: {platform}/{platform_id}（{result.get('message')}）")
        return None

    items = [x for x in result["data"] if isinstance(x, dict) and x.get("accountId")]
    if not items:
        return None

    # 当前账号：defaultAccount == accountId（与 bind-list 模板同款判定），
    # 兼容 defaultId 布尔标记，都取不到就退回第一条
    chosen = None
    for item in items:
        mark = item.get("defaultAccount")
        if mark is not None and str(mark) == str(item.get("accountId")):
            chosen = item
            break
    if chosen is None:
        chosen = next((x for x in items if x.get("defaultId")), None) or items[0]

    identity = {
        "account_id": str(chosen.get("accountId")),
        "user_name": str(chosen.get("userName") or ""),
        "server": str(chosen.get("server") or ""),
    }
    _IDENTITY_CACHE[key] = identity
    _identity_cached_at[key] = time.monotonic()
    logger.info(f"解析到绑定账号: {identity}")
    return identity


async def resolve_account_id(server: str, nickname: str) -> int | None:
    """按「服务器 + 原始昵称」查 AID，查不到返回 ``None``。

    ▍为什么需要原字符串
    上游的昵称搜索是精确匹配，而 hikari 的指令解析按空白切词（全角空格 U+3000
    也算空白），括号重拼又只接半角空格。实测 ``Alice　Smith`` 能命中，``Alice Smith``
    回「用户不存在」——所以带全角空格的昵称不能用指令传，只能先换成 AID。
    """
    if not server or not nickname:
        return None
    url = f"{hikari_config.yuyuko_url}/public/wows/account/search/{server}/user"
    try:
        from hikari_core.core.http_client import get_client_yuyuko
        from hikari_core.core.model import Hikari_Model

        client = await get_client_yuyuko(Hikari_Model().UserInfo)
        resp = await client.post(url, json={"userName": nickname, "one": True}, timeout=20)
        result = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"按昵称解析 AID 失败: {e!r}")
        return None
    if result.get("code") != 200 or not result.get("data"):
        logger.info(f"昵称搜索未命中: {server}/{nickname!r}（{result.get('message')}）")
        return None
    try:
        return int(result["data"][0]["accountId"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _with_identity(command_text: str, identity: dict[str, str] | None) -> str:
    """把「服务器 + 昵称」前置到指令前，让 hikari 走查别人的正常路径。

    同时**去掉 ``me``**：``me`` 是「查自己」的标记，注入真实身份后再留着它，
    hikari 会因为它与「服务器+昵称」并存而判成「未识别的指令」
    （``me asia Nahida_official`` → 无法路由）。
    身份已由前缀表达，去掉即可；总表这类「只有 me」的命令因此变成纯身份查询，
    正好走 hikari 的默认账号查询分支。
    """
    if not identity or not identity.get("user_name"):
        return command_text
    tokens = [t for t in command_text.split() if t.lower() != "me"]
    head = " ".join(x for x in (identity.get("server"), identity.get("user_name")) if x)
    return " ".join([head, *tokens]).strip()


# ===========================================================================
# 指令文本拼装
#
# hikari 的指令解析口径（commands/parser.py）：
#   - `me` 或缺省 -> 查自己（自动落到该用户已绑定的账号）
#   - `<服务器> <昵称> <指令>` -> 查别人（顺序不限，服务器名走关键字匹配）
# 所以「查自己」和「查别人」只需要决定要不要把 server/nickname 拼进去。
# ===========================================================================

# 服务器名规范写法（与 hikari constants.servers 的关键字一致）
ASTRBOT_SERVER_ALIASES = {
    "亚服": "亚服", "asia": "亚服",
    "国服": "国服", "cn": "国服", "china": "国服",
    "欧服": "欧服", "eu": "欧服", "europe": "欧服",
    "美服": "美服", "na": "美服", "us": "美服",
    "俄服": "俄服", "ru": "俄服", "russia": "俄服",
}


def normalize_server(text: str | None) -> str | None:
    """把服务器说法归一到 hikari 认识的关键字；识别不了就原样返回。"""
    if not text:
        return None
    key = str(text).strip().lower()
    return ASTRBOT_SERVER_ALIASES.get(key) or str(text).strip()


def _identity_tokens(server: str | None, nickname: str | None) -> list[str]:
    """查别人时的「服务器 + 昵称」前缀；没给昵称就返回空（= 查自己）。

    实测 hikari 只认「服务器 + 昵称」两段：只给昵称会把它当成服务器名，
    报 ``accountId 不允许为空``。所以查别人必须两个都给，缺服务器的错误
    由话术层（``guide._ERROR_GUIDES``）换成给模型的行动指引。
    """
    tokens: list[str] = []
    if nickname:
        if server:
            tokens.append(normalize_server(server) or server)
        tokens.append(str(nickname).strip())
    return tokens


def build_account_command(server: str | None = None, nickname: str | None = None) -> str:
    """查总表：``me`` / ``<服务器> <昵称>``。

    ▍为什么查自己必须显式带上 ``me``
    hikari 的指令路由要靠 ``_is_identity_query`` 识别「无指令关键词的身份查询」，
    而它对**空列表返回 False**（见 ``commands/router.py``）——
    也就是说空命令会被判成「未识别的指令，请发送 wws help」，
    根本走不到总表逻辑。所以查自己时必须显式给 ``me``。
    """
    tokens = _identity_tokens(server, nickname)
    return " ".join(tokens) if tokens else "me"


def build_ship_command(
    ship: str,
    server: str | None = None,
    nickname: str | None = None,
) -> str:
    """查单船：``单船 <船名>`` / ``<服务器> <昵称> 单船 <船名>``。"""
    return " ".join([*_identity_tokens(server, nickname), "ship", str(ship)])


def build_recent_command(
    days: int | None = None,
    server: str | None = None,
    nickname: str | None = None,
    mode: str = "recent",
) -> str:
    """查近期战绩。

    Args:
        mode: hikari 的指令词 —— ``recent``（随机+排位）/ ``recent_random``（仅随机）
            / ``recent_rank``（仅排位）。
    """
    parts = [*_identity_tokens(server, nickname), mode]
    if days:
        parts.append(str(days))
    return " ".join(parts)


def build_ship_recent_command(
    ship: str,
    days: int | None = None,
    server: str | None = None,
    nickname: str | None = None,
) -> str:
    """查单船近期：``ship recent <船名> [天数]``。

    ▍注意不是 ``ship.recent``
    hikari 的指令表里只有 ``ship.rank`` 这个带点的，单船近期是 **ship 的二级指令**
    （``router.py`` 的 ``ship_command_list = [command(('recent', '近期'), get_ShipRecent)]``），
    所以要写成两个词 ``ship recent``。写成 ``ship.recent`` 会报「未识别的指令」。
    """
    parts = [*_identity_tokens(server, nickname), "ship", "recent", str(ship)]
    if days:
        parts.append(str(days))
    return " ".join(parts)


def build_ships_command(
    min_level: int | None = None,
    max_level: int | None = None,
    nation: str | None = None,
    ship_type: str | None = None,
    server: str | None = None,
    nickname: str | None = None,
) -> str:
    """筛选查询：``ships <国家> <类型> min N max M``（顺序不限，缺省项不拼）。"""
    parts = [*(_identity_tokens(server, nickname)), "ships"]
    if nation:
        parts.append(str(nation))
    if ship_type:
        parts.append(str(ship_type))
    if min_level is not None:
        parts += ["min", str(min_level)]
    if max_level is not None:
        parts += ["max", str(max_level)]
    return " ".join(parts)


def build_bind_command(server: str, nickname: str) -> str:
    """绑定账号：``bind <服务器> <昵称>``（两个都必填，否则 hikari 会报参数错误）。

    昵称带空格要整体加半角括号：hikari 对 bind 只取第一个 token 当昵称，
    ``bind cn John Doe`` 会安静地绑成 ``John``。
    """
    name = str(nickname)
    if any(ch.isspace() for ch in name) and not (name.startswith("(") and name.endswith(")")):
        name = f"({name})"
    return " ".join(["bind", normalize_server(server) or server, name])


def build_roll_command(
    nation: str | None = None,
    ship_type: str | None = None,
    level: int | None = None,
) -> str:
    """随机战舰：``roll [国家] [类型] [等级]``，都不给就是全随机。"""
    parts = ["roll"]
    if nation:
        parts.append(str(nation))
    if ship_type:
        parts.append(str(ship_type))
    if level is not None:
        parts.append(str(level))
    return " ".join(parts)


# 命令字面量映射：固定参数的查询直接查表，省得在 main.py 里散落魔法字符串。
# 需要拼参数（身份 / 天数 / 筛选条件）的用上面的函数。
COMMAND_BUILDERS: dict[str, str] = {
    "recent_random": "recent_random",
    "recent_rank": "recent_rank",
    "recents": "recents",
    "bind_list": "bind_list",
    "bind_change": "change_bind",
}


def build_literal_command(
    key: str,
    server: str | None = None,
    nickname: str | None = None,
) -> str:
    """无额外参数的查询：``<服务器> <昵称> <指令>``。"""
    return " ".join(
        part for part in (*_identity_tokens(server, nickname), COMMAND_BUILDERS[key]) if part
    )


# ===========================================================================
# 查询执行
# ===========================================================================


@dataclass
class QueryResult:
    """一次工具查询的结果。

    Attributes:
        ok: 查询是否成功拿到数据
        data: 给 LLM 的精简字段（成功时）
        message: 给 LLM 的错误/提示文本（失败或需引导时）
        hikari: 原始 Hikari_Model，调用方据此渲染发图
        select_options: 需要用户二次选择时的候选列表
        need_select: 是否在等选择
        error_kind: 故障分类（network / exception），仅异常路径会有值
    """

    ok: bool = False
    data: Any = field(default_factory=dict)
    message: str = ""
    hikari: Hikari_Model | None = None
    select_options: list = field(default_factory=list)
    need_select: bool = False
    error_kind: str = ""


class QueryRunner:
    """执行一次 hikari 查询并按查询类型提取字段。"""

    def __init__(self, platform: str, platform_id: str, bot_id: str, group_id: str | None = None):
        self.platform = platform
        self.platform_id = platform_id
        self.bot_id = bot_id
        self.group_id = group_id

    async def run(
        self,
        command_text: str,
        extractor: str,
        *,
        select_index: int | None = None,
        battle_mode: str | None = None,
        inject_identity: bool = False,
        prefer_exact: str | None = None,
        **extract_kwargs: Any,
    ) -> QueryResult:
        """执行查询。

        Args:
            command_text: 拼好的 hikari 指令文本（不带 ``wws``）
            extractor: 用哪个提取方法，见 ``_EXTRACTORS``
            select_index: 二次选择时用户给出的序号
            battle_mode: 只要某个战斗模式（None = 全部，即默认行为）
            inject_identity: 是否是「查自己」的查询。
                为 True 时插件先解析绑定账号，把「服务器+昵称」拼进指令，
                绕开 hikari 部分功能在 me 模式下拿平台 ID 当 accountId 的问题；
                解析不到绑定则返回「未绑定」提示（让模型引导用户去绑定）。
            prefer_exact: 船名搜索命中多条时，自动选中与它完全同名的那条
                （见 :func:`match_exact_index`）。
            extract_kwargs: 透传给提取方法的额外参数
        """
        result = QueryResult()
        try:
            identity = None
            if inject_identity:
                identity = await resolve_identity(self.platform, self.platform_id)
                if identity is None:
                    result.ok = False
                    result.message = (
                        "该用户似乎还没绑定窝窝屎账号，请引导 TA 用 wws_bind 工具绑定后再查询"
                    )
                    return result

            # select_index 是「上一轮返回了候选项，这一轮带序号继续」的分支
            if select_index is not None:
                hikari = await self._run_with_select(command_text, select_index, identity)
            else:
                hikari = await init_hikari_no_output(
                    command_text=_with_identity(command_text, identity),
                    platform=self.platform,
                    PlatformId=self.platform_id,
                    BotId=self.bot_id,
                    GroupId=self.group_id,
                )
                hikari = await self._retry_variant_search(hikari, command_text, identity, prefer_exact)
                hikari = await self._auto_pick_exact(hikari, prefer_exact)
            result.hikari = hikari

            # 候选对不上用户写的船名：先确认，别把换皮 / 原版的战绩混着报出去
            if select_index is None:
                confirm = self._confirm_mismatch(hikari, prefer_exact)
                if confirm:
                    result.message = confirm
                    return result

            if hikari.Status == 'wait':
                # 需要用户挑一个：把候选项交给 LLM 转述，不在这里阻塞等待
                result.need_select = True
                result.select_options = hikari.Input.Select_Data or []
                result.message = format_select_options(result.select_options)
                return result

            if hikari.Status != 'success':
                # error / failed：Output.Data 就是给用户看的错误文案
                result.message = str(hikari.Output.Data)
                return result

            raw = hikari.Output.Data
            mode = normalize_battle_mode(battle_mode)
            result.data = self._extract(extractor, raw, only=mode, **extract_kwargs)
            result.ok = True
            return result
        except Exception as e:
            logger.exception(f"LLM 工具查询异常: {e}")
            # 分类交给上层决定话术：网络波动提示重试，程序异常引导加群反馈
            result.ok = False
            result.error_kind = classify_error(e)
            result.message = str(e) or type(e).__name__
            return result

    async def render(self, result: QueryResult) -> bytes | None:
        """把查询结果渲染成图片；不需要渲染或渲染失败返回 None。

        单独一步是因为：提取字段只需要原始 dict，而渲染要等提取完再做
        （``output_hikari`` 会就地替换 ``Output.Data``）。
        """
        hikari = result.hikari
        if hikari is None or not result.ok:
            return None
        try:
            hikari = await output_hikari(hikari)
        except Exception as e:
            # output_hikari 内部通常已兜住异常并转成 error 状态，这里只防空
            logger.warning(f"LLM 工具渲染失败: {e}")
            return None
        if hikari is not result.hikari:
            result.hikari = hikari
        data = hikari.Output.Data
        return data if isinstance(data, bytes) else None

    async def _run_with_select(
        self,
        command_text: str,
        select_index: int,
        identity: dict[str, str] | None = None,
    ) -> Hikari_Model:
        """带序号的二次调用：先重建 wait 状态，再执行下一步。

        ▍为什么不用 ``callback_hikari``
        它内部会 ``return await output_hikari(hikari)``，而 ``output_hikari`` 在
        渲染之后会把 ``hikari.Output.Data`` **换成图片字节**（``__init__.py`` 里
        ``hikari.Output.Data = await html_to_pic(...)``）。那样提取器拿到的就是
        PNG 二进制，二次选择后的数据全丢。
        这里改走底层 ``hikari.Function(hikari)``：语义与 callback 一致
        （都是执行下一步），但不会用渲染结果覆盖数据；渲染由 ``render()``
        单独负责。
        """
        hikari = await init_hikari_no_output(
            command_text=_with_identity(command_text, identity),
            platform=self.platform,
            PlatformId=self.platform_id,
            BotId=self.bot_id,
            GroupId=self.group_id,
        )
        if hikari.Status != 'wait':
            return hikari
        options = hikari.Input.Select_Data or []
        if not (1 <= int(select_index) <= len(options)):
            return hikari.failed(f"序号超出范围，可选 1-{len(options)}")
        hikari.Input.Select_Index = int(select_index)
        if not hikari.Function:
            return hikari.error('缺少请求方法')
        return await hikari.Function(hikari)

    async def _auto_pick_exact(
        self, hikari: Hikari_Model, prefer_exact: str | None
    ) -> Hikari_Model:
        """候选里挑得出唯一目标时直接选中，不再让用户挑。

        hikari 的船名搜索是**包含匹配**：``克莱贝尔`` 会同时命中 ``克莱贝尔CLR``，
        ``信浓`` 会同时命中 ``AL信浓``，命令模式下会让用户回序号。聊天里多一问
        很别扭；而「原皮 + 别名/简写/英文名」本来就该直接能查，所以这里替用户挑
        （规则见 :func:`pick_ship_index`），只有真挑不出来才把列表交回去。
        """
        if not prefer_exact or hikari.Status != 'wait' or not hikari.Function:
            return hikari
        index = pick_ship_index(hikari.Input.Select_Data or [], prefer_exact)
        if index is None:
            return hikari
        logger.info(f"LLM 工具自动选中第 {index} 条船（输入 {prefer_exact!r}）")
        hikari.Input.Select_Index = index
        return await hikari.Function(hikari)

    async def _retry_variant_search(
        self,
        hikari: Hikari_Model,
        command_text: str,
        identity: dict[str, str] | None,
        prefer_exact: str | None,
    ) -> Hikari_Model:
        """船名带换皮标签却搜不到时，拆掉标签用核心名重搜一次。

        ▍为什么需要重试
        上游搜索是**包含匹配**：库里叫 ``北卡罗来纳CLR``，用户写 ``北卡CLR`` 时
        这个字符串不是库中名字的子串，上游直接返回 0 条。去掉标签只拿核心名
        ``北卡`` 去搜就能命中，之后再按标签在候选里挑（见 :func:`pick_ship_index`）。

        只在**确实找不到船**时重试；「查询不到战绩数据」「账号查不到」等其它失败
        保持原样返回，不要多打一次接口。
        """
        if not prefer_exact or not command_text:
            return hikari
        if hikari.Status not in ('failed', 'error'):
            return hikari
        if '找不到船' not in str(hikari.Output.Data or ''):
            return hikari
        parts = split_variant_name(prefer_exact)
        if not parts:
            return hikari
        core = parts[0]
        retried_text = _swap_ship_token(command_text, prefer_exact, core)
        if not retried_text:
            return hikari
        logger.info(f"LLM 工具船名带换皮标签但上游无结果，改用核心名重搜: {prefer_exact!r} -> {core!r}")
        return await init_hikari_no_output(
            command_text=_with_identity(retried_text, identity),
            platform=self.platform,
            PlatformId=self.platform_id,
            BotId=self.bot_id,
            GroupId=self.group_id,
        )

    @staticmethod
    def _confirm_mismatch(hikari: Hikari_Model, prefer_exact: str | None) -> str | None:
        """候选对不上用户写的船名时，先确认再查。

        两种情况：

        - **用户写了换皮标签**（``北卡CLR``）：只接受同标签的船。库里有就放行；
          没有就别拿原版（``北卡罗来纳``）顶替 —— 直接告诉模型「没这条换皮船」。
        - **用户没写标签**（``信浓``）：唯一命中的是换皮船时先确认
          （``信浓`` → 只剩 ``AL信浓``，不能把联动船当成原版报出去）；
          命中的是原版则视为别名 / 简写 / 英文名，直接放行。
        """
        if not prefer_exact:
            return None
        variant = split_variant_name(prefer_exact)
        if variant is not None:
            tag = variant[1]
            if hikari.Status == 'wait':
                available = list(hikari.Input.Select_Data or [])
            elif hikari.Status == 'success':
                available = [hikari.Input.ShipInfo] if hikari.Input.ShipInfo else []
            else:
                return None
            if not available or any(_variant_core_of(x, tag) for x in available):
                return None
            names = "、".join(
                str(x.get("nameCn") or x.get("nameEnglish") or "?")
                for x in available[:6]
                if isinstance(x, dict)
            )
            return (
                f"没有查到带「{tag.upper()}」标记的换皮船「{prefer_exact}」。"
                f"库里同名或近名的只有：{names}。"
                f"请向用户确认是要查原版（用原船名重新调用），还是船名写错了；"
                f"不要把原版的数据当成换皮船回答。"
            )

        if hikari.Status != 'success':
            return None
        ship = hikari.Input.ShipInfo
        if not ship:
            return None
        target = _normalize_name(prefer_exact)
        target_compact = _compact_name(prefer_exact)
        if any(
            _normalize_name(ship.get(key) or "") == target
            or _compact_name(ship.get(key) or "") == target_compact
            for key in _SHIP_NAME_KEYS
        ):
            return None
        if not is_variant_ship(ship):
            # 不是换皮 → 当成别名/简写/英文名，直接查
            return None
        name = ship.get("nameCn") or ship.get("nameEnglish") or "该船"
        detail = " ".join(
            str(x) for x in ((f"{ship.get('level')} 级" if ship.get('level') else ""), ship.get("shipType")) if x
        )
        picked = f"{name}（{detail}）" if detail else str(name)
        return (
            f"没有与「{prefer_exact}」同名的原版船，只匹配到换皮船 {picked}。"
            f"请向用户确认是不是要查这条；确认后用 ship=\"{name}\" 重新调用本工具，"
            f"不要直接拿这条的数据当原船作答。"
        )

    @staticmethod
    def _extract(
        extractor: str, raw: Any, *, only: str | None = None, **kwargs: Any
    ) -> Any:
        """按名字调用提取器；未知名字返回空 dict 而不是抛异常。"""
        ext = BattleStatsExtractor
        if extractor == "account":
            return ext.account_summary(raw)
        if extractor == "ship":
            return ext.ship_summary(raw)
        if extractor == "recent":
            return ext.recent_summary(raw, only=only)
        if extractor == "ship_recent":
            return ext.ship_recent_summary(raw, only=only)
        if extractor == "recents":
            return ext.recents_summary(raw)
        if extractor == "ships":
            return ext.ships_summary(raw)
        if extractor == "roll":
            return ext.roll_summary(raw)
        if extractor == "bind":
            # 绑定类接口的返回有三种：绑定列表 / 「绑定成功」这类消息 / 其它结构，
            # 统一交给 bind_result 归一化（字符串就直接用，列表就压成精简账号表）
            return ext.bind_result(raw)
        return {}


def _normalize_name(name: Any) -> str:
    """比对船名用：去掉空白（含 ``\\xa0``）、统一大小写。"""
    return "".join(str(name).split()).casefold()


def _compact_name(name: Any) -> str:
    """更宽松的比对键：去掉重音与所有非字母数字（``Z52`` = ``Z-52``，``Kleber`` = ``Kléber``）。"""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _swap_ship_token(command_text: str, ship: str, replacement: str) -> str | None:
    """把指令里的船名换成别的写法；找不到原船名返回 ``None``。

    从右往左找：船名总在指令尾部（``ship recent 北卡CLR 1`` 里后面只跟天数），
    用 ``rfind`` 可以避免误改前面恰好同名的昵称。
    """
    index = command_text.rfind(ship)
    if index < 0:
        return None
    return command_text[:index] + replacement + command_text[index + len(ship):]


# 船名搜索返回的条目里可用来比对「同名」的字段（中文名 / 英文名）
_SHIP_NAME_KEYS = ("nameCn", "nameEnglish", "name", "shipName", "shipNameEn")

# 换皮（同模换色 / 联动）船的命名标签：AL信浓、克莱贝尔CLR、ARP大和……
# 前缀要求后面是空格或中文，避免把 Alabama 之类当成 AL 联动船。
_VARIANT_PREFIXES = ("AL", "ARP", "HSF", "BA", "WK", "TM", "DMM", "IGN", "VII", "40K")
_VARIANT_SUFFIXES = ("CLR", "TE", "AR", "BF", "LNY")


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def split_variant_name(name: Any) -> tuple[str, str] | None:
    """把船名拆成「去掉标签的核心名 + 小写标签」，没有换皮标签返回 ``None``。

    ``北卡CLR`` -> ``("北卡", "clr")``、``AL 信浓`` -> ``("信浓", "al")``、
    ``North Carolina CLR`` -> ``("North Carolina", "clr")``。

    标签两侧必须是空格或中文（``Alabama`` 不能被当成 AL 联动船）。
    """
    text = str(name or "").strip()
    if not text:
        return None
    upper = text.upper()
    for tag in _VARIANT_SUFFIXES:
        if upper.endswith(tag) and len(text) > len(tag):
            prev = text[-len(tag) - 1]
            if prev.isspace() or _is_cjk(prev):
                core = text[: -len(tag)].strip()
                if core:
                    return core, tag.lower()
    for tag in _VARIANT_PREFIXES:
        if upper.startswith(tag) and len(text) > len(tag):
            nxt = text[len(tag)]
            if nxt.isspace() or _is_cjk(nxt):
                core = text[len(tag):].strip()
                if core:
                    return core, tag.lower()
    return None


def _has_variant_tag(name: Any) -> bool:
    """船名是否带换皮标签（AL信浓 / 克莱贝尔CLR / AL Bismarck）。"""
    text = str(name or "").strip()
    if not text:
        return False
    if split_variant_name(text):
        return True
    return text.upper() in _ALL_VARIANT_TAGS


_ALL_VARIANT_TAGS = frozenset(_VARIANT_PREFIXES) | frozenset(_VARIANT_SUFFIXES)


def is_variant_ship(item: Any) -> bool:
    """候选项是否是换皮船（任一名字字段带换皮标签即算）。"""
    if not isinstance(item, dict):
        return False
    return any(_has_variant_tag(item.get(key)) for key in _SHIP_NAME_KEYS)


def _variant_core_of(item: Any, tag: str) -> str | None:
    """候选项在指定换皮标签下的核心名（紧凑写法）；不是该标签的换皮船返回 ``None``。

    一个候选有多个名字字段（中文 / 英文 / 360 客户端名），取最短的那个核心名 ——
    ``克莱贝尔CLR`` 与 ``Kléber CLR`` 都会归一成能跟简写比对的形式。
    """
    if not isinstance(item, dict):
        return None
    best: str | None = None
    for key in _SHIP_NAME_KEYS:
        parts = split_variant_name(item.get(key))
        if not parts or parts[1] != tag:
            continue
        compact = _compact_name(parts[0])
        if compact and (best is None or len(compact) < len(best)):
            best = compact
    return best


def pick_ship_index(options: list, name: str) -> int | None:
    """在候选里挑出用户真正要的那条，返回 1 起始序号；挑不出来返回 None。

    分两种情况：**输入带换皮标签**（``北卡CLR``）和**不带**（``北卡``）。

    带标签时只认同标签的换皮船：

    1. 核心名与输入核心名完全相同（``克莱贝尔CLR``）
    2. 核心名以输入核心名开头（``北卡CLR`` → ``北卡罗来纳CLR``），且唯一 / 最短
    3. 找不到同标签的船 → None（不能让原版顶替，宁可交回候选或让上层提示）

    不带标签时（原皮允许别名 / 简写 / 英文名）：

    1. **严格同名**：忽略空格与大小写（``克莱贝尔`` → ``克莱贝尔``，不会落到 CLR/TE）
    2. **紧凑同名**：去重音、去非字母数字（``Z52`` → ``Z-52``，``Kleber`` → ``Kléber``）
    3. 去掉换皮船后只剩一条 → 就是它（别名 / 简写：``北卡`` → ``北卡罗来纳``）
    4. 原皮有多条时，优先名字以输入开头的（``阿达尔`` → ``阿达尔伯特亲王``）；
       仍不唯一则看科技树船（``groupType=upgradeable``）是否唯一
    5. 仍无法唯一确定 → None，交回候选列表让用户选
    """
    if not isinstance(name, str) or not name.strip():
        return None
    target = _normalize_name(name)
    target_compact = _compact_name(name)
    if not target_compact:
        return None

    def names_of(item: dict) -> list[str]:
        return [str(item.get(key) or "") for key in _SHIP_NAME_KEYS]

    strict = [
        index
        for index, item in enumerate(options, start=1)
        if isinstance(item, dict) and any(_normalize_name(n) == target for n in names_of(item))
    ]
    if len(strict) == 1:
        return strict[0]

    compact = [
        index
        for index, item in enumerate(options, start=1)
        if isinstance(item, dict)
        and any(_compact_name(n) == target_compact for n in names_of(item))
    ]
    if len(compact) == 1:
        return compact[0]

    variant = split_variant_name(name)
    if variant is not None:
        core_compact = _compact_name(variant[0])
        tag = variant[1]
        if not core_compact:
            return None
        scored: list[tuple[int, int, int]] = []
        for index, item in enumerate(options, start=1):
            core = _variant_core_of(item, tag)
            if not core or not core.startswith(core_compact):
                continue
            scored.append((0 if core == core_compact else 1, len(core), index))
        if not scored:
            return None
        scored.sort()
        if len(scored) == 1 or scored[0][:2] != scored[1][:2]:
            return scored[0][2]
        return None

    base = [
        index
        for index, item in enumerate(options, start=1)
        if isinstance(item, dict) and not is_variant_ship(item)
    ]
    if len(base) == 1:
        return base[0]
    if len(base) > 1:
        prefixed = [
            index
            for index in base
            if any(_compact_name(n).startswith(target_compact) for n in names_of(options[index - 1]))
        ]
        pool = prefixed or base
        if len(pool) == 1:
            return pool[0]
        tech_tree = [
            index
            for index in pool
            if str(options[index - 1].get("groupType") or "").lower() == "upgradeable"
        ]
        if len(tech_tree) == 1:
            return tech_tree[0]
    return None


def format_select_options(options: list, limit: int = 20) -> str:
    """把候选项格式化成给 LLM 的带序号列表。

    形状来自 hikari 的 ``Select_Data``：船是 ``{nameCn, shipType, level}``，
    绑定账号是 ``{accountId, server, userName}``，军团是 ``{tag, name}``；
    三种都做宽松解析（``ShipInfo`` 本身是 dict 子类，用 ``get`` 即可）。
    """
    if not options:
        return "没有可选项。"
    lines = ["这一步需要用户选择，请把下面的列表转述给用户并让 TA 回复序号："]
    for index, item in enumerate(options[:limit], start=1):
        if isinstance(item, dict):
            name = (
                item.get("nameCn")
                or item.get("name")
                or item.get("tag")
                or item.get("userName")
                or "?"
            )
            if item.get("userName"):
                # 绑定账号：服务器 + AID 才能区分同名号
                extras = (item.get("server"), item.get("accountId"))
            else:
                # 船 / 军团：等级 + 舰种 / TAG
                extras = (item.get("level"), item.get("shipType") or item.get("tag"))
            detail = " ".join(str(x) for x in extras if x not in (None, ""))
            lines.append(f"  {index}. {name}{('（' + detail + '）') if detail else ''}")
        else:
            lines.append(f"  {index}. {item}")
    if len(options) > limit:
        lines.append(f"  …（共 {len(options)} 项，仅列出前 {limit} 项）")
    lines.append("用户选定后，用同样的参数再次调用本工具，并填 select_index=序号。")
    return "\n".join(lines)
