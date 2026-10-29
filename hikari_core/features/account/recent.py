import json

from loguru import logger

from hikari_core.core.config import hikari_config
from hikari_core.core.http_client import get_client_yuyuko
from hikari_core.core.http_error_handler import handle_yuyuko_errors
from hikari_core.core.model import Hikari_Model
from hikari_core.core.template_registry import Templates
from hikari_core.features.api import check_yuyuko_cache, get_AccountIdByName


# from nonebot_plugin_htmlrender import html_to_pic


@handle_yuyuko_errors()
async def get_RecentInfo(hikari: Hikari_Model) -> Hikari_Model:
    """查询近期（随机 + 排位）"""
    return await _query_recent_day(hikari, mode='all')


@handle_yuyuko_errors()
async def get_RecentRandom(hikari: Hikari_Model) -> Hikari_Model:
    """查询近期随机（仅 PVP 随机战）"""
    return await _query_recent_day(hikari, mode='random')


@handle_yuyuko_errors()
async def get_RecentRank(hikari: Hikari_Model) -> Hikari_Model:
    """查询近期排位（仅 RANK_SOLO 排位战）"""
    return await _query_recent_day(hikari, mode='rank')


async def _query_recent_day(hikari: Hikari_Model, mode: str = 'all') -> Hikari_Model:
    """近期战绩公共查询逻辑。

    Args:
        mode: all=随机+排位 / random=仅随机(PVP) / rank=仅排位(RANK_SOLO)
    """
    if hikari.Status == 'init':
        if hikari.Input.Search_Type == 3:
            account_id = await get_AccountIdByName(hikari, hikari.Input.Server, hikari.Input.AccountName)
            # 查不到时 get_AccountIdByName 已把状态置为 failed（消息在 Output.Data），直接返回它；
            # 不要再把返回值塞进 f-string —— 失败时它就是 hikari 自己，会造成模型自引用
            if account_id is None:
                return hikari if hikari.Status == 'failed' else hikari.error('查询账号失败，请稍后重试或确认昵称是否正确')
            hikari.Input.AccountId = account_id
    else:
        return hikari.error('当前请求状态错误')
    if hikari.Input.Search_Type == 3:
        is_cache = await check_yuyuko_cache(hikari, hikari.Input.Server, hikari.Input.AccountId)
    else:
        is_cache = await check_yuyuko_cache(hikari, hikari.Input.Platform, hikari.Input.PlatformId)
    if is_cache:
        logger.success('上报数据成功')
    else:
        logger.success('跳过上报数据，直接请求')
    url = f'{hikari_config.yuyuko_url}/api/wows/recent/day/info'
    if hikari.Input.Search_Type == 3:
        params = {
            'server': hikari.Input.Server,
            'accountId': hikari.Input.AccountId,
            'dateTime': hikari.Input.Recent_Date,
            'day': hikari.Input.Recent_Day,
            'shipId': 0,
        }
    else:
        params = {
            'server': hikari.Input.Platform,
            'accountId': hikari.Input.PlatformId,
            'dateTime': hikari.Input.Recent_Date,
            'day': hikari.Input.Recent_Day,
            'shipId': 0,
        }
    print(params)
    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.get(url, params=params, timeout=20)
    result = json.loads(resp.content)
    hikari.Output.Yuyuko_Code = result['code']
    if result['code'] != 200:
        return hikari.failed(f"{result['message']}")

    battle_type_info = result['data']['battleTypeInfo']
    has_pvp = battle_type_info['PVP']['battle']
    has_rank = battle_type_info['RANK_SOLO']['battle']
    if mode == 'random':
        if not has_pvp:
            return hikari.failed('该日期没有随机战数据记录')
        template = Templates.WWS_INFO_RECENT_RANDOM
    elif mode == 'rank':
        if not has_rank:
            return hikari.failed('该日期没有排位战数据记录')
        template = Templates.WWS_INFO_RECENT_RANK
    else:
        if not (has_pvp or has_rank):
            return hikari.failed('该日期数据记录不存在')
        template = Templates.WWS_INFO_RECENT
    hikari = template.apply_to(hikari)
    return hikari.success(result['data'])
