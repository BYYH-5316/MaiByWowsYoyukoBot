import asyncio
import gzip
import json
import traceback
from base64 import b64encode
from typing import List

from httpx import PoolTimeout
from loguru import logger

from hikari_core.core.config import hikari_config
from hikari_core.core.http_client import (
    get_client_wg,
    get_client_yuyuko,
    recreate_client_wg,
    recreate_client_yuyuko,
)
from hikari_core.core.http_error_handler import handle_yuyuko_errors
from hikari_core.core.model import Hikari_Model, ShipInfo


async def get_nation_list(hikari: Hikari_Model):
    try:
        msg = ''
        url = f'{hikari_config.yuyuko_url}/public/wows/encyclopedia/nation/list'
        client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
        resp = await client_yuyuko.get(url, timeout=20)
        result = json.loads(resp.content)
        for nation in result['data']:
            msg: str = msg + f"{nation['cn']}：{nation['nation']}\n"
        return msg
    except PoolTimeout:
        await recreate_client_yuyuko()
        return
    except Exception:
        logger.error(traceback.format_exc())


@handle_yuyuko_errors()
async def get_ship_name(hikari: Hikari_Model):
    """根据国家等级类型查船名"""
    msg = ''
    params = {
        'country': hikari.Input.ShipInfo.country,
        'level': hikari.Input.ShipInfo.level,
        'shipName': '',
        'shipType': hikari.Input.ShipInfo.shipType,
        'groupType': 'default',
    }
    url = f'{hikari_config.yuyuko_url}/public/wows/encyclopedia/ship/search'
    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.get(url, params=params, timeout=20)
    result = json.loads(resp.content)
    if result['data']:
        for ship in result['data']:
            msg += f"{ship['nameCn']}：{ship['nameEnglish']}\n"
        return hikari.success(msg)
    else:
        return hikari.failed('没有符合的船只')


async def get_ship_byName(hikari: Hikari_Model) -> List:
    """搜索战舰"""
    try:
        ship_name = hikari.Input.ShipInfo.nameCn
        ship_name_select_index = None
        result = ship_name.split('.')
        if len(result) == 2 and result[1].isdigit():
            ship_name = result[0]
            ship_name_select_index = int(result[1])
        url = f'{hikari_config.yuyuko_url}/public/wows/encyclopedia/ship/search'
        params = {'country': '', 'level': '', 'shipName': ship_name, 'shipType': '', 'groupType': 'default'}
        client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
        resp = await client_yuyuko.get(url, params=params, timeout=20)
        result = json.loads(resp.content)
        if result.get('code') == 200 and result.get('data'):
            code_data = result['data']
            # 转换为 ShipInfo 对象列表
            ship_list = []
            for ship_dict in code_data:
                ship_list.append(ShipInfo(ship_dict))
            # 如果指定了序号选择
            if ship_name_select_index and ship_name_select_index <= len(ship_list):
                return [ship_list[ship_name_select_index - 1]]

            return ship_list
        else:
            return []
    except PoolTimeout:
        await recreate_client_yuyuko()


async def get_user_ship_byName(hikari: Hikari_Model) -> List:
    """搜索用户战舰"""
    try:
        ship_name = hikari.Input.ShipInfo.nameCn
        ship_name_select_index = None
        result = ship_name.split('.')
        if len(result) == 2 and result[1].isdigit():
            ship_name = result[0]
            ship_name_select_index = int(result[1])
        url = f'{hikari_config.yuyuko_url}/public/wows/encyclopedia/userShip/search'
        params = {'country': '', 'level': '', 'shipName': ship_name, 'shipType': '', 'groupType': 'default'}
        client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
        resp = await client_yuyuko.get(url, params=params, timeout=20)
        result = json.loads(resp.content)
        if result.get('code') == 200:
            code_data = result['data']
            # 转换为 ShipInfo 对象列表
            ship_list = []
            for ship_dict in code_data:
                ship_list.append(ShipInfo(ship_dict))
            # 如果指定了序号选择
            if ship_name_select_index and ship_name_select_index <= len(ship_list):
                return [ship_list[ship_name_select_index - 1]]
            return ship_list
        else:
            hikari.failed(result['message'])
            return []
    except PoolTimeout:
        await recreate_client_yuyuko()


async def get_AccountIdByName(hikari: Hikari_Model, server: str, name: str) -> int | None:
    """按昵称查 AID：成功返回 int，查不到返回 None（同时把 hikari 置为 failed）。

    失败时不要把 hikari.failed(...) 的返回值交出去：它就是 hikari 自己，
    调用方写成 hikari.Input.AccountId = await get_AccountIdByName(...) 时，
    模型会通过 Input.AccountId 持有自身引用，之后任何 str()/repr()/f-string
    都会无限展开抛 RecursionError。
    """
    try:
        url = f'{hikari_config.yuyuko_url}/public/wows/account/search/{server}/user'
        params = {'userName': name, 'one': True}
        client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
        resp = await client_yuyuko.post(url, json=params, timeout=20)
        result = json.loads(resp.content)
        if result['code'] == 200 and result['data']:
            return int(result['data'][0]['accountId'])
        # 只把状态落到 hikari 上，返回值必须是 None —— 交回模型会引发自引用
        hikari.failed(result['message'])
        return None
    except PoolTimeout:
        await recreate_client_yuyuko()
        return None


async def get_ClanIdByName(hikari: Hikari_Model, server: str, tag: str):
    try:
        url = f'{hikari_config.yuyuko_url}/public/wows/clan/search/{server}'
        params = {
            'tag': tag,
        }
        client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
        resp = await client_yuyuko.get(url, params=params, timeout=20)
        result = json.loads(resp.content)
        if result['code'] == 200 and result['data']:
            return result['data']
        else:
            return None
    except PoolTimeout:
        await recreate_client_yuyuko()
        return


async def check_yuyuko_cache(hikari: Hikari_Model, server, id):
    try:
        yuyuko_cache_url = f'{hikari_config.yuyuko_url}/api/wows/cache/check'
        params = {'accountId': id, 'server': server}
        client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
        resp = await client_yuyuko.post(yuyuko_cache_url, json=params, timeout=5)
        result = json.loads(resp.content)
        cache_data = {}
        if result['code'] == 201:
            if 'DEV' in result['data']:
                await get_wg_info(cache_data, 'DEV', result['data']['DEV'])
            elif 'PVP' in result['data']:
                tasks = []
                for key in result['data']:
                    tasks.append(asyncio.ensure_future(get_wg_info(cache_data, key, result['data'][key])))
                await asyncio.gather(*tasks)
            if not cache_data:
                return False
            data_base64 = b64encode(gzip.compress(json.dumps(cache_data))).decode()
            params['data'] = data_base64
            resp = await client_yuyuko.post(yuyuko_cache_url, json=params, timeout=5)
            result = json.loads(resp.content)
            logger.success(result)
            if result['code'] == 200:
                return True
            else:
                return False
        return False
    except PoolTimeout:
        await recreate_client_yuyuko()
        return False


async def get_wg_info(params, key, url):
    try:
        client_wg = await get_client_wg()
        resp = await client_wg.get(url, timeout=5, follow_redirects=True)
        wg_result = json.loads(resp.content)
        if resp.status_code == 200 and wg_result['status'] == 'ok':
            params[key] = resp.text
    except PoolTimeout:
        await recreate_client_wg()
