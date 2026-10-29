import html
import time
import traceback
from datetime import datetime
from typing import Optional

from loguru import logger

from .router import _is_identity_query, render_suggest_message, route_command
from ..core.admin import get_pending_check_token, is_admin, verify_and_add_admin
from ..core.constants import levels, nations, servers, shiptypes
from ..core.model import Hikari_Model
from ..core.utils import match_keywords
from ..features.account.info import get_AccountInfo
from ..features.account.recent import get_RecentInfo, get_RecentRandom, get_RecentRank
from ..features.account.recents import get_RecentsInfo
from ..features.account.ships import get_Ships
from ..features.api import get_ship_name
from ..features.bind import (
    change_BindInfo,
    delete_BindInfo,
    get_BindInfo,
    set_BindInfo,
    set_special_BindInfo,
    update_user_cache,
)
from ..features.clan.cw_rank import get_CwRank
from ..features.clan.cw_recent import get_cw_recent
from ..features.clan.info import get_ClanInfo
from ..features.clan.rank import get_ClanRank
from ..features.fun import check_christmas_box, get_BanInfo, get_sx_info, roll_ship
from ..features.ship.info import get_ShipInfo
from ..features.ship.rank import get_ShipRank
from ..features.ship.recent import get_ShipRecent


async def analyze_command(hikari: Hikari_Model) -> Hikari_Model:
    try:
        if hikari.Status == 'init':
            if not hikari.Input.Command_Text:
                return hikari.error('请发送wws help查看帮助')
            hikari.Input.Command_Text = html.unescape(str(hikari.Input.Command_Text)).strip()
            # 管理员校验：把启动时控制台输出的 32 位校验串直接发送给机器人即可（建议私信）。
            # 发送者已在全局管理员缓存中则直接放行，不进入校验流程。
            admin_result = await _try_verify_admin(hikari)
            if admin_result is not None:
                return admin_result
            hikari.Input.Command_List = hikari.Input.Command_Text.split()
            hikari.Input.Command_List = _merge_paren_groups(hikari.Input.Command_List)
            hikari.Function, hikari.Input.Command_List, suggest = await route_command(hikari.Input.Command_List)
            if suggest:
                # 指令未识别，给出相似命令的智能提示
                return hikari.error(render_suggest_message(suggest))
            # 未匹配到任何指令且不是合法身份查询（me / 服务器+昵称）：给出提示
            if hikari.Function == get_AccountInfo and not _is_identity_query(hikari.Input.Command_List):
                return hikari.error('未识别的指令，请发送 wws help 查看帮助')
            hikari = await extract_with_me(hikari)
            hikari = await extract_with_function(hikari)
        return hikari
    except Exception:
        logger.error(traceback.format_exc())
        return hikari.error('解析指令时发生错误，请确认输入参数无误')


def _merge_paren_groups(command_list: list) -> list:
    """把被半角括号 () 包裹的内容合并为单个 token（去掉括号）。

    游戏昵称/船名不会包含 ()，故 (AI deal) 无歧义地表示一个含空格的昵称；
    仅当 token 以 '(' 开头才视为分组开始，单个 token 内部带括号不受影响。
    """
    merged = []
    i, n = 0, len(command_list)
    while i < n:
        tok = command_list[i]
        if tok == '()':
            i += 1
            continue
        if tok.startswith('('):
            if tok.endswith(')') and len(tok) > 2:
                # 单 token 分组：(AI) -> AI
                merged.append(tok[1:-1])
                i += 1
                continue
            parts = [tok[1:]]
            i += 1
            closed = False
            while i < n:
                cur = command_list[i]
                if cur.endswith(')'):
                    parts.append(cur[:-1])
                    i += 1
                    closed = True
                    break
                parts.append(cur)
                i += 1
            if closed:
                merged.append(' '.join(p for p in parts if p))
            else:
                merged.append('(' + ' '.join(parts))
        else:
            merged.append(tok)
            i += 1
    return merged


async def _try_verify_admin(hikari: Hikari_Model) -> Optional[Hikari_Model]:
    """管理员校验拦截：消息内容等于待校验串时完成验证+写入，返回结果；否则返回 None 继续正常解析。"""
    try:
        # 已在全局管理员缓存中 → 无需校验，直接放行正常指令解析
        if is_admin(hikari.UserInfo.PlatformId):
            return None
        token = get_pending_check_token()
        if not token:
            return None
        if hikari.Input.Command_Text != token:
            return None
        # 验证 + 写入统一入口（一步完成）
        if verify_and_add_admin(hikari.UserInfo.PlatformId, token):
            return hikari.success('管理员添加成功，现在可以使用 check_version / update_style / update_ship')
        return hikari.failed('管理员添加失败，请检查缓存目录写入权限')
    except Exception:
        logger.error(traceback.format_exc())
        return None


# ============================================================
# 身份解析（单一入口）
# ============================================================

# 不参与"统一服务器前置提取"的功能：
# - roll / search_ship：参数词汇（国别词）与服务器关键词存在重叠——'europe' 既是
#   欧服服务器词又是 欧洲 国别词（'Russia' 仅是服务器词，苏联的国别关键词是
#   'ussr'/'苏联'，不重叠），全局提取会把 'roll europe …' 的国别误吞成服务器；
# - 绑定族：历史遗留语义（special_bind 走 AID、me+服务器 旧写法、纯数字序号等），
#   待产品收敛（见 docs 设计债 5），维持各自的独立解析。
_NO_SERVER_SLOT_FUNCS = frozenset({
    roll_ship,
    get_ship_name,
    get_BindInfo,
    set_BindInfo,
    set_special_BindInfo,
    change_BindInfo,
    delete_BindInfo,
})


async def extract_with_me(hikari: Hikari_Model) -> Hikari_Model:
    """身份解析（单一入口，路由守卫与解析共用同一口径）：显式 me / 服务器+昵称 / me 缺省。

    - 显式 me：Search_Type=1，平台身份指向发送者；
    - 存在服务器关键词且功能允许服务器槽位（见 _NO_SERVER_SLOT_FUNCS）：
      前置提取到 Input.Server 并从 Command_List 移除，Search_Type=3——
      此后各 _handle_* 不再各自重复 match 服务器（消除口径分叉）；
    - 均无（有具体指令匹配且非兜底账号查询）：me 缺省，Search_Type=1。
    @提及 由平台侧转换为 me，SDK 内部不再解析。
    """
    try:
        for i in list(hikari.Input.Command_List):
            if str(i).lower() == 'me':
                hikari.Input.Search_Type = 1
                _set_identity(hikari)
                hikari.Input.Command_List.remove(i)
                break
        if hikari.Input.Search_Type != 3:
            return hikari
        server, rest = await match_keywords(list(hikari.Input.Command_List), servers)
        if server and hikari.Function not in _NO_SERVER_SLOT_FUNCS:
            hikari.Input.Server = server
            hikari.Input.Command_List = rest
        elif not server and hikari.Function is not get_AccountInfo:
            # me 可缺省：仅当有具体指令匹配（非兜底账号查询）且未指定服务器时，默认查自己
            hikari.Input.Search_Type = 1
            _set_identity(hikari)
        return hikari
    except Exception:
        logger.error(traceback.format_exc())
        return hikari.error('解析指令时发生错误，请确认输入参数无误')


# ============================================================
# extract_with_function 的辅助函数
# ============================================================

def _set_identity(hikari: Hikari_Model) -> None:
    """将 Platform 和 PlatformId 设置为当前用户的信息。"""
    hikari.Input.Platform = hikari.UserInfo.Platform
    hikari.Input.PlatformId = hikari.UserInfo.PlatformId


def _pop_digits_from_list(command_list: list) -> list:
    """从列表中移除并返回所有 ≤3 位的数字元素。"""
    to_delete = []
    for item in command_list:
        if str(item).isdigit() and len(str(item)) <= 3:
            to_delete.append(item)
    for item in to_delete:
        command_list.remove(item)
    return to_delete


# ============================================================
# 各功能组处理函数
# ============================================================

async def _handle_account_recent(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_AccountInfo / get_RecentInfo / get_RecentRandom / get_RecentRank / get_RecentsInfo / get_ShipInfo / get_ShipRecent。"""
    hikari.Input.Recent_Date = time.strftime('%Y-%m-%d', time.localtime())

    if hikari.Function in [get_RecentInfo, get_RecentRandom, get_RecentRank] and datetime.now().hour < 7:
        hikari.Input.Recent_Day = 1

    if hikari.Function in [get_RecentInfo, get_RecentRandom, get_RecentRank, get_RecentsInfo, get_ShipRecent]:
        _extract_day_and_date_params(hikari)

    if hikari.Function in [get_AccountInfo, get_RecentInfo, get_RecentRandom, get_RecentRank, get_RecentsInfo]:
        return await _parse_account_query_params(hikari)
    elif hikari.Function in [get_ShipInfo, get_ShipRecent]:
        return await _parse_ship_query_params(hikari)
    return hikari


def _extract_day_and_date_params(hikari: Hikari_Model) -> None:
    """从命令列表中提取天数或日期参数并存入 Input。"""
    to_delete = []
    for item in hikari.Input.Command_List:
        if str(item).isdigit() and len(str(item)) <= 3:
            hikari.Input.Recent_Day = int(item)
            to_delete.append(item)
        try:
            time.strptime(str(item), '%Y-%m-%d')
            hikari.Input.Recent_Date = str(item)
            to_delete.append(item)
            hikari.Input.Recent_Day = 0
        except ValueError:
            continue
    for item in to_delete:
        hikari.Input.Command_List.remove(item)


async def _parse_account_query_params(hikari: Hikari_Model) -> Hikari_Model:
    """解析 Search_Type == 3 时的昵称参数（服务器已在 extract_with_me 前置提取到 Input.Server）。"""
    if hikari.Input.Search_Type != 3:
        return hikari
    if not hikari.Input.Server:
        return hikari.error('服务器名输入错误')
    if len(hikari.Input.Command_List) != 1:
        return hikari.error('您似乎准备用游戏昵称查询水表，请检查参数中是否包含服务器和游戏昵称，以空格分隔，顺序不限')
    hikari.Input.AccountName = str(hikari.Input.Command_List[0])
    return hikari


async def _parse_ship_query_params(hikari: Hikari_Model) -> Hikari_Model:
    """解析单船查询：服务器+昵称+船名，或 me 模式（剩余 token 合并为完整船名，支持多词英文船名如 Jean Bart）。

    服务器已在 extract_with_me 前置提取到 Input.Server。
    """
    if hikari.Input.Server:
        remaining = hikari.Input.Command_List
        if len(remaining) != 2:
            return hikari.error('您似乎准备用服务器+昵称查询单船战绩，请检查参数是否缺少或溢出，以空格分隔')
        hikari.Input.AccountName = str(remaining[0])
        hikari.Input.ShipInfo.nameCn = str(remaining[1])
    elif hikari.Input.Command_List:
        # me 模式（显式 me 或未指定服务器时默认查自己）：剩余 token 合并为完整船名
        hikari.Input.Search_Type = 1
        _set_identity(hikari)
        hikari.Input.ShipInfo.nameCn = ' '.join(str(i) for i in hikari.Input.Command_List)
    else:
        return hikari.error('您似乎准备用me查询单船战绩，请检查是否缺少船名')
    return hikari


async def _handle_ships(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_Ships：ships 后面必须至少一个参数。

    参数仅识别 等级 / 地区 / 战舰类型（constants 匹配并替换为规范值），
    min/max 必须后跟数字且均可缺省（默认 min=5, max=0）。
    先解析 min/max，避免其后的数字被当作等级匹配。
    """
    command_list = list(hikari.Input.Command_List)

    # 服务器 + 昵称模式：服务器已在 extract_with_me 前置提取（Input.Server），剩余第一个为昵称
    if hikari.Input.Server:
        if len(command_list) < 2:
            return hikari.error('ships 请携带 服务器 + 昵称 + 筛选条件，如 ships 亚服 昵称 bb 10')
        hikari.Input.AccountName = str(command_list[0])
        command_list = command_list[1:]

    # ships 后面必须至少一个参数（等级/地区/类型 或 min/max 均可）
    if not command_list:
        return hikari.error('ships 后面必须带参数，如 ships bb 10 japan min 6 max 10')

    # 先解析 min / max（必须后跟数字，均可缺省，默认 min=5, max=0）
    min_level = 5
    max_level = 0
    rest = []
    i = 0
    while i < len(command_list):
        tok = str(command_list[i]).lower()
        if tok in ('min', 'max'):
            if i + 1 >= len(command_list) or not str(command_list[i + 1]).isdigit():
                return hikari.error(f'{tok} 后面必须跟数字，如 {tok} 6')
            if tok == 'min':
                min_level = int(command_list[i + 1])
            else:
                max_level = int(command_list[i + 1])
            i += 2
        else:
            rest.append(command_list[i])
            i += 1
    hikari.Input.ShipsMin = min_level
    hikari.Input.ShipsMax = max_level
    command_list = rest

    # 等级 / 地区 / 战舰类型：命中即替换为 constants 中的规范值
    hikari.Input.ShipInfo.country, command_list = await match_keywords(command_list, nations)
    hikari.Input.ShipInfo.shipType, command_list = await match_keywords(command_list, shiptypes)
    hikari.Input.ShipInfo.level, command_list = await match_keywords(command_list, levels)

    if command_list:
        return hikari.error(f'无法识别参数: {command_list[0]}，仅支持 等级/地区/战舰类型 与 min/max')
    return hikari


async def _handle_bind(hikari: Hikari_Model) -> Hikari_Model:
    """处理所有绑定操作：查询 / 设置 / 特殊绑定 / 切换 / 删除。"""
    if hikari.Function == get_BindInfo:
        if hikari.Input.Search_Type != 1:
            return hikari.error('参数似乎出了问题呢，请使用me查询绑定')
        return hikari

    if hikari.Function in [set_BindInfo, set_special_BindInfo]:
        return await _handle_set_bind(hikari)

    if hikari.Function in [change_BindInfo, delete_BindInfo]:
        return await _handle_change_delete_bind(hikari)

    return hikari


async def _handle_set_bind(hikari: Hikari_Model) -> Hikari_Model:
    """处理 set_BindInfo 和 set_special_BindInfo。"""
    if hikari.Input.Search_Type != 3 and len(hikari.Input.Command_List) != 2:
        return hikari.error('参数似乎输错了呢，请确保后面跟随服务器+游戏昵称')

    hikari.Input.Server, hikari.Input.Command_List = await match_keywords(hikari.Input.Command_List, servers)
    if not hikari.Input.Server:
        return hikari.error('服务器名输入错误')

    if hikari.Function == set_BindInfo:
        hikari.Input.AccountName = str(hikari.Input.Command_List[0])
    elif hikari.Function == set_special_BindInfo and hikari.Input.Command_List[0].isdigit():
        hikari.Input.AccountId = int(hikari.Input.Command_List[0])
    else:
        return hikari.error('请在网页版复制正确的特殊绑定指令，地址：https://wows.mgaia.top')

    _set_identity(hikari)
    return hikari


async def _handle_change_delete_bind(hikari: Hikari_Model) -> Hikari_Model:
    """处理 change_BindInfo 和 delete_BindInfo。"""
    if len(hikari.Input.Command_List) not in [0, 1]:
        return hikari.error('请检查是否仅输入了要切换的序号，也可为空进入选择列表')

    if len(hikari.Input.Command_List) == 1:
        digits = _pop_digits_from_list(hikari.Input.Command_List)
        if digits:
            hikari.Input.Select_Index = int(digits[0])

    _set_identity(hikari)
    return hikari


async def _handle_update_user_cache(hikari: Hikari_Model) -> Hikari_Model:
    """处理 update_user_cache：wws me update / wws 服务器 游戏昵称 update（服务器已前置提取）。"""
    if hikari.Input.Search_Type == 1:
        return hikari  # me：目标为默认绑定账号
    if hikari.Input.Search_Type != 3 or not hikari.Input.Server:
        return hikari.error('请使用 wws me update 或 wws 服务器 游戏昵称 update')
    if len(hikari.Input.Command_List) != 1:
        return hikari.error('您似乎准备更新指定账号缓存，请检查参数中是否包含服务器和游戏昵称，以空格分隔，顺序不限')
    hikari.Input.AccountName = str(hikari.Input.Command_List[0])
    return hikari


async def _handle_ban_box(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_BanInfo / get_sx_info / check_christmas_box（服务器已前置提取）。"""
    if hikari.Input.Search_Type != 3:
        return hikari
    if not hikari.Input.Server or len(hikari.Input.Command_List) != 1:
        return hikari.error('您似乎准备用游戏昵称查询，请检查参数中是否包含服务器和游戏昵称，以空格分隔，顺序不限')
    hikari.Input.AccountName = str(hikari.Input.Command_List[0])
    return hikari


async def _handle_ship_name_roll(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_ship_name 和 roll_ship — 匹配国家、舰种、等级。"""
    hikari.Input.ShipInfo.country, hikari.Input.Command_List = await match_keywords(hikari.Input.Command_List, nations)
    if not hikari.Input.ShipInfo.country and hikari.Function == get_ship_name:
        return hikari.error('请检查国家名是否正确')

    hikari.Input.ShipInfo.shipType, hikari.Input.Command_List = await match_keywords(hikari.Input.Command_List, shiptypes)
    if not hikari.Input.ShipInfo.shipType and hikari.Function == get_ship_name:
        return hikari.error('请检查船只类别是否正确')

    hikari.Input.ShipInfo.level, hikari.Input.Command_List = await match_keywords(hikari.Input.Command_List, levels)
    if not hikari.Input.ShipInfo.level and hikari.Function == get_ship_name:
        return hikari.error('请检查船只等级是否正确')
    return hikari


async def _handle_ship_rank(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_ShipRank（服务器已前置提取到 Input.Server）。"""
    if not hikari.Input.Server:
        if len(hikari.Input.Command_List) == 1:
            return hikari.error('请检查参数中是否包含服务器和船名，以空格分隔，顺序不限')
        return hikari.error('服务器名输入错误')
    if len(hikari.Input.Command_List) != 1:
        return hikari.error('请检查参数中是否包含服务器和船名，以空格分隔，顺序不限')
    hikari.Input.ShipInfo.nameCn = str(hikari.Input.Command_List[0])
    return hikari


async def _handle_clan_info(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_ClanInfo / get_ClanRank（服务器已前置提取到 Input.Server）。"""
    if hikari.Input.Search_Type != 3:
        return hikari
    if not hikari.Input.Server:
        if len(hikari.Input.Command_List) == 1:
            return hikari.error('您似乎准备用公会TAG查询水表，请检查参数中是否包含服务器和公会TAG，以空格分隔，顺序不限')
        return hikari.error('服务器名输入错误')
    if len(hikari.Input.Command_List) != 1:
        return hikari.error('您似乎准备用公会TAG查询水表，请检查参数中是否包含服务器和公会TAG，以空格分隔，顺序不限')
    hikari.Input.ClanName = str(hikari.Input.Command_List[0])
    return hikari


async def _handle_cw(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_CwRank 和 get_cw_recent。"""
    if hikari.Function == get_CwRank:
        return await _handle_cw_rank(hikari)
    elif hikari.Function == get_cw_recent:
        return await _handle_cw_recent(hikari)
    return hikari


async def _handle_cw_rank(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_CwRank — 服务器 + 可选赛季号（服务器已前置提取）。"""
    if len(hikari.Input.Command_List) == 1:
        digits = _pop_digits_from_list(hikari.Input.Command_List)
        if digits:
            hikari.Input.CwSeasonId = int(digits[0])
    elif len(hikari.Input.Command_List) > 1:
        return hikari.error('您似乎准备查询CW排行榜，请确认是否仅输入了赛季和服务器，留空为最新赛季和全服')
    return hikari


async def _handle_cw_recent(hikari: Hikari_Model) -> Hikari_Model:
    """处理 get_cw_recent — 按名称搜索或按身份搜索。"""
    if hikari.Input.Search_Type == 3:
        return await _parse_cw_recent_by_name(hikari)
    else:
        return await _parse_cw_recent_by_identity(hikari)


async def _parse_cw_recent_by_name(hikari: Hikari_Model) -> Hikari_Model:
    """通过 服务器 + 公会TAG 解析 CW 近期战绩参数（服务器已前置提取）。"""
    if not hikari.Input.Server:
        return hikari.error('服务器名输入错误')

    rest = hikari.Input.Command_List
    hikari.Input.ClanName = str(rest[0])
    remaining = len(rest)

    if remaining == 2:
        hikari.Input.CwSeasonId = int(rest[1])
    elif remaining == 3:
        hikari.Input.CwSeasonId = int(rest[1])
        hikari.Input.Recent_Day = int(rest[2])
    else:
        return hikari.error('请检查参数中是否包含服务器、公会TAG、赛季数字、团队数字(可选)')
    return hikari


async def _parse_cw_recent_by_identity(hikari: Hikari_Model) -> Hikari_Model:
    """通过当前用户身份（me）解析 CW 近期战绩参数。"""
    hikari.Input.Server = hikari.UserInfo.Platform
    hikari.Input.ClanId = hikari.UserInfo.PlatformId

    remaining = len(hikari.Input.Command_List)
    if remaining == 1:
        hikari.Input.CwSeasonId = int(hikari.Input.Command_List[0])
    elif remaining == 2:
        hikari.Input.CwSeasonId = int(hikari.Input.Command_List[0])
        hikari.Input.Recent_Day = int(hikari.Input.Command_List[1])
    else:
        return hikari.error('请检查参数中是否赛季数字、团队数字(可选)')
    return hikari


# ============================================================
# 功能分发表与主分发函数
# ============================================================

_HANDLERS = {
    get_AccountInfo: _handle_account_recent,
    get_RecentInfo: _handle_account_recent,
    get_RecentRandom: _handle_account_recent,
    get_RecentRank: _handle_account_recent,
    get_RecentsInfo: _handle_account_recent,
    get_Ships: _handle_ships,
    get_ShipInfo: _handle_account_recent,
    get_ShipRecent: _handle_account_recent,
    get_BindInfo: _handle_bind,
    set_BindInfo: _handle_bind,
    set_special_BindInfo: _handle_bind,
    change_BindInfo: _handle_bind,
    delete_BindInfo: _handle_bind,
    update_user_cache: _handle_update_user_cache,
    get_BanInfo: _handle_ban_box,
    get_sx_info: _handle_ban_box,
    check_christmas_box: _handle_ban_box,
    get_ship_name: _handle_ship_name_roll,
    roll_ship: _handle_ship_name_roll,
    get_ShipRank: _handle_ship_rank,
    get_ClanInfo: _handle_clan_info,
    get_ClanRank: _handle_clan_info,  # clan.rank / rank clan / clan rank：与 clan 信息同为 服务器+公会TAG 绑定
    get_CwRank: _handle_cw,
    get_cw_recent: _handle_cw,
}


async def extract_with_function(hikari: Hikari_Model) -> Hikari_Model:
    """根据 hikari.Function 将指令分发到对应的解析处理函数。"""
    try:
        handler = _HANDLERS.get(hikari.Function)
        if handler:
            return await handler(hikari)
        return hikari
    except Exception:
        logger.error(traceback.format_exc())
        return hikari.error('解析指令时发生错误，请确认输入参数无误')
