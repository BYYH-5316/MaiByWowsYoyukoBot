import json

from hikari_core.core.config import hikari_config
from hikari_core.core.http_client import get_client_yuyuko
from hikari_core.core.http_error_handler import handle_yuyuko_errors
from hikari_core.core.model import Hikari_Model
from hikari_core.core.template_registry import Templates
from hikari_core.features.api import get_ClanIdByName


@handle_yuyuko_errors()
async def get_cw_recent(hikari: Hikari_Model) -> Hikari_Model:
    """查询公会cw赛季战斗记录"""
    if hikari.Status == 'init':
        if hikari.Input.Search_Type == 3:
            clanList = await get_ClanIdByName(hikari, hikari.Input.Server, hikari.Input.ClanName)
            if clanList:
                if len(clanList) < 2:
                    hikari.Input.ClanId = clanList[0]['clanId']
                else:
                    hikari.Input.Select_Data = clanList
                    Templates.SELECT_CLAN.apply_to(hikari)
                    return hikari.wait(clanList)
            else:
                return hikari.failed('找不到军团，请确认军团名是否正确')
    elif hikari.Status == 'wait':
        if hikari.Input.Select_Data and hikari.Input.Select_Index and hikari.Input.Select_Index <= len(hikari.Input.Select_Data):
            hikari.Input.ClanId = hikari.Input.Select_Data[hikari.Input.Select_Index - 1]['clanId']
        else:
            return hikari.error('请选择有效的序号')
    else:
        return hikari.error('当前请求状态错误')

    # if hikari.Input.Search_Type == 3:
    #    is_cache = await check_yuyuko_cache(hikari.Input.Server, hikari.Input.AccountId)
    # else:
    #    is_cache = await check_yuyuko_cache(hikari.Input.Platform, hikari.Input.PlatformId)
    # if is_cache:
    #    logger.success('上报数据成功')
    # else:
    #    logger.success('跳过上报数据，直接请求')

    url = f'{hikari_config.yuyuko_url}/public/wows/clan/battle/info'
    params = {
        'season': hikari.Input.CwSeasonId,
        'server': hikari.Input.Server,
        'clanId': hikari.Input.ClanId,
        'team': hikari.Input.Recent_Day,
    }

    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.get(url, params=params, timeout=20)
    result = json.loads(resp.content)
    hikari.Output.Yuyuko_Code = result['code']
    if result['code'] == 200:
        if result['data']:
            Templates.WWS_CLAN_CW.apply_to(hikari)
        else:
            return hikari.failed('没有战斗数据')
        return hikari.success(result['data'])
    else:
        return hikari.failed(f"{result['message']}")
