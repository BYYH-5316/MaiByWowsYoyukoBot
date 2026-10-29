import json

from hikari_core.core.config import hikari_config
from hikari_core.core.http_client import get_client_yuyuko
from hikari_core.core.http_error_handler import handle_yuyuko_errors
from hikari_core.core.model import Hikari_Model
from hikari_core.core.template_registry import Templates
from hikari_core.features.api import get_ship_byName


@handle_yuyuko_errors()
async def get_ShipRank(hikari: Hikari_Model):
    """查询单船排行榜"""
    if hikari.Status == 'init':
        shipList = await get_ship_byName(hikari)
        if shipList:
            if len(shipList) < 2:
                hikari.Input.ShipInfo = shipList[0]
            else:
                hikari.Input.Select_Data = shipList
                Templates.SELECT_SHIP.apply_to(hikari)
                return hikari.wait(shipList)
        else:
            if hikari.Status == 'failed':
                return hikari
            return hikari.failed('找不到船，请确认船名是否正确，可以使用【wws 查船名】查询船只中英文')
    elif hikari.Status == 'wait':
        if hikari.Input.Select_Data and hikari.Input.Select_Index and hikari.Input.Select_Index <= len(hikari.Input.Select_Data):
            hikari.Input.ShipInfo = hikari.Input.Select_Data[hikari.Input.Select_Index - 1]
        else:
            return hikari.error('请选择有效的序号')
    else:
        return hikari.error('当前请求状态错误')

    return await search_rank(hikari)


# 静态资源地址前缀（与 features/system.py 的 ship_cache 缓存命名保持一致）
_SHIP_STATIC_BASE = f'{hikari_config.yuyuko_url}/nahida-static/ship_cache'


def _enrich_ship_display(ship_info) -> dict:
    """补齐排行榜顶部所需的战舰图（imgSmall）。

    图鉴搜索接口若未返回 imgSmall，则按静态资源命名规则合成 URL
    （渲染时 find_and_modify_shipinfo 会自动替换为本地缓存路径）。
    """
    server_type = str(ship_info.get('serverType') or '').lower()
    ship_id = ship_info.get('shipId') or ''
    if not ship_info.get('imgSmall') and server_type and ship_id:
        ship_info['imgSmall'] = f'{_SHIP_STATIC_BASE}/{server_type}-{ship_id}-small.png'
    return ship_info


@handle_yuyuko_errors()
async def search_rank(hikari: Hikari_Model):
    url = f'{hikari_config.yuyuko_url}/public/rank/yuyuko_ship/{hikari.Input.Server}?shipId={hikari.Input.ShipInfo.shipId}&page=1'
    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.get(url, timeout=20)
    result = json.loads(resp.content)
    if result['code'] == 200 and result['data']:
        Templates.SHIP_RANK.apply_to(hikari)
        result_data = {'data': result['data'], 'shipInfo': _enrich_ship_display(hikari.Input.ShipInfo)}
        return hikari.success(result_data)
    else:
        return hikari.failed(f"{result['message']}")
