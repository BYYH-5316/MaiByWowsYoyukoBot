import json

from hikari_core.core.config import hikari_config
from hikari_core.core.constants import nations, shiptypes
from hikari_core.core.http_client import get_client_yuyuko
from hikari_core.core.http_error_handler import handle_yuyuko_errors
from hikari_core.core.model import Hikari_Model
from hikari_core.core.template_registry import Templates
from hikari_core.features.api import get_AccountIdByName


def _cn_of(value, lists) -> str:
    """取 constants 中规范值对应的中文别名，用于展示。"""
    if not value:
        return ''
    for m in lists:
        if m.match_keywords == value:
            for kw in m.keywords:
                if any('\u4e00' <= ch <= '\u9fff' for ch in kw):
                    return kw
    return value


def _filter_desc(ship_info, min_level: int, max_level: int) -> str:
    """拼接筛选条件的中文描述。"""
    parts = []
    if ship_info.level:
        parts.append(f"{ship_info.level}级")
    if ship_info.shipType:
        parts.append(_cn_of(ship_info.shipType, shiptypes))
    if ship_info.country:
        parts.append(_cn_of(ship_info.country, nations))
    if min_level:
        parts.append(f'等级≥{min_level}')
    if max_level:
        parts.append(f'等级≤{max_level}')
    return ' · '.join(parts) or '全部'


@handle_yuyuko_errors()
async def get_Ships(hikari: Hikari_Model) -> Hikari_Model:
    """筛选查询账号战舰列表（等级 / 地区 / 战舰类型 / min / max）。"""
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
        server = hikari.Input.Server
        account_id = hikari.Input.AccountId
    else:
        server = hikari.Input.Platform
        account_id = hikari.Input.PlatformId

    url = f'{hikari_config.yuyuko_url}/public/wows/account/ship/info/query_list'
    params = {
        'server': server,
        'accountId': account_id,
        'shipType': hikari.Input.ShipInfo.shipType or '',
        'country': hikari.Input.ShipInfo.country or '',
        'level': hikari.Input.ShipInfo.level or '',
        'min': hikari.Input.ShipsMin,
        'max': hikari.Input.ShipsMax,
    }
    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.get(url, params=params, timeout=20)
    result = json.loads(resp.content)
    hikari.Output.Yuyuko_Code = result['code']
    if result['code'] == 200 and result['data']:
        raw = result['data']
        ship_list = _normalize_ship_list(raw)
        user_info = raw.get('userInfo') if isinstance(raw, dict) else None
        battle_type_info = raw.get('battleTypeInfo') if isinstance(raw, dict) else None
        filter_info = {
            'shipType': hikari.Input.ShipInfo.shipType or '',
            'country': hikari.Input.ShipInfo.country or '',
            'level': hikari.Input.ShipInfo.level or '',
            'min': hikari.Input.ShipsMin,
            'max': hikari.Input.ShipsMax,
            'desc': _filter_desc(hikari.Input.ShipInfo, hikari.Input.ShipsMin, hikari.Input.ShipsMax),
        }
        Templates.WWS_SHIPS.apply_to(hikari)
        return hikari.success({
            'list': ship_list,
            'filter': filter_info,
            'userInfo': user_info,
            'battleTypeInfo': battle_type_info,
        })
    else:
        return hikari.failed(f"{result['message']}")


def _normalize_ship_list(data) -> list:
    """兼容接口返回的多种容器形状，归一为列表。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('value', 'list', 'shipInfoBattleList'):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []
