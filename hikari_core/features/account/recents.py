import json

from hikari_core.core.config import hikari_config
from hikari_core.core.http_client import get_client_yuyuko
from hikari_core.core.http_error_handler import handle_yuyuko_errors
from hikari_core.core.model import Hikari_Model
from hikari_core.core.template_registry import Templates
from hikari_core.features.api import get_AccountIdByName


# from nonebot_plugin_htmlrender import html_to_pic


@handle_yuyuko_errors()
async def get_RecentsInfo(hikari: Hikari_Model) -> Hikari_Model:
    """查询Recents"""
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
    url = f'{hikari_config.yuyuko_url}/api/wows/recents/day/info'
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
    if result['code'] == 200:
        hikari = Templates.WWS_INFO_RECENTS.apply_to(hikari)
        return hikari.success(result['data'])
    else:
        return hikari.failed(f"{result['message']}")
