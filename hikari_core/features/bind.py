import json

from hikari_core.core.config import hikari_config
from hikari_core.core.http_client import get_client_yuyuko
from hikari_core.core.http_error_handler import handle_yuyuko_errors
from hikari_core.core.model import Hikari_Model
from hikari_core.core.template_registry import Templates
from hikari_core.features.api import get_AccountIdByName


@handle_yuyuko_errors()
async def get_BindInfo(hikari: Hikari_Model) -> Hikari_Model:
    """获取用户绑定信息"""
    if hikari.Status != 'init':
        return hikari.error('当前请求状态错误')
    url = f'{hikari_config.yuyuko_url}/api/user/platform/bind/list'
    params = {'platformType': hikari.Input.Platform, 'platformId': hikari.Input.PlatformId}
    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.get(url, params=params, timeout=20)
    result = json.loads(resp.content)
    if result['code'] == 200 and result['message'] == 'success':
        if result['data']:
            hikari = Templates.BIND_LIST.apply_to(hikari)
            return hikari.success(result['data'])
        else:
            return hikari.failed('该用户似乎还没绑定窝窝屎账号')
    else:
        return hikari.failed(f"{result['message']}")


@handle_yuyuko_errors()
async def set_BindInfo(hikari: Hikari_Model) -> Hikari_Model:
    """通过昵称绑定账号"""
    if hikari.Status == 'init':
        if hikari.Input.Search_Type == 3 and not hikari.Input.AccountId:
            account_id = await get_AccountIdByName(hikari, hikari.Input.Server, hikari.Input.AccountName)
            # 查不到时 get_AccountIdByName 已把状态置为 failed（消息在 Output.Data），直接返回它；
            # 不要再把返回值塞进 f-string —— 失败时它就是 hikari 自己，会造成模型自引用
            if account_id is None:
                return hikari if hikari.Status == 'failed' else hikari.error('查询账号失败，请稍后重试或确认昵称是否正确')
            hikari.Input.AccountId = account_id
    else:
        return hikari.error('当前请求状态错误')
    url = f'{hikari_config.yuyuko_url}/api/user/platform/switch/bind'
    params = {'platformType': hikari.Input.Platform, 'platformId': hikari.Input.PlatformId, 'accountId': hikari.Input.AccountId}
    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.post(url, json=params, timeout=20)
    result = json.loads(resp.content)
    if result['code'] == 200 and result['message'] == 'success':
        return hikari.success('绑定成功')
    else:
        return hikari.failed(f"{result['message']}")


# 防止混淆纯数字名与AID，单独添加特殊绑定指令
@handle_yuyuko_errors()
async def set_special_BindInfo(hikari: Hikari_Model) -> Hikari_Model:
    """通过AID绑定账号"""
    if hikari.Status != 'init':
        return hikari.error('当前请求状态错误')
    url = f'{hikari_config.yuyuko_url}/api/user/platform/switch/bind'
    params = {'platformType': hikari.Input.Platform, 'platformId': hikari.Input.PlatformId, 'accountId': hikari.Input.AccountId}
    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.post(url, json=params, timeout=20)
    result = json.loads(resp.content)
    if result['code'] == 200 and result['message'] == 'success':
        return hikari.success('绑定成功')
    else:
        return hikari.failed(f"{result['message']}")


@handle_yuyuko_errors()
async def change_BindInfo(hikari: Hikari_Model) -> Hikari_Model:
    """切换绑定"""

    if hikari.Status not in ['init', 'wait']:
        return hikari.error('当前请求状态错误')
    # 初次调用时无参数，返回输出选择列表
    if hikari.Status == 'init' and not hikari.Input.Select_Index:
        hikari = await get_BindInfo(hikari)
        # 成功获取绑定列表时置为wait，否则按原状态返回
        if hikari.Status == 'success':
            hikari.Status = 'wait'
            hikari.Input.Select_Data = hikari.Output.Data
        return hikari
    # 初次调用时或回调时有参数
    elif hikari.Input.Select_Index:
        # 初次调用时有选择序号，查询一次绑定列表
        if not hikari.Input.Select_Data:
            hikari.Status = 'init'
            hikari = await get_BindInfo(hikari)
            if not hikari.Status == 'success':
                return hikari
            hikari.Input.Select_Data = hikari.Output.Data
        if hikari.Input.Select_Index > len(hikari.Input.Select_Data):
            return hikari.error('请选择正确的序号')
        hikari.Input.AccountId = hikari.Input.Select_Data[hikari.Input.Select_Index - 1]['accountId']
        hikari.Status = 'init'
        # 切换绑定
        hikari = await set_BindInfo(hikari)
        if hikari.Status == 'success':
            return hikari.success(
                f"切换绑定成功,当前绑定账号{hikari.Input.Select_Data[hikari.Input.Select_Index - 1]['server']}：{hikari.Input.Select_Data[hikari.Input.Select_Index - 1]['userName']}"
            )


@handle_yuyuko_errors()
async def delete_BindInfo(hikari: Hikari_Model) -> Hikari_Model:
    """删除绑定"""
    if hikari.Status not in ['init', 'wait']:
        return hikari.error('当前请求状态错误')
    # 初次调用时无参数，返回输出选择列表
    if hikari.Status == 'init' and not hikari.Input.Select_Index:
        hikari = await get_BindInfo(hikari)
        # 成功获取绑定列表时置为wait，否则按原状态返回
        if hikari.Status == 'success':
            hikari.Status = 'wait'
            hikari.Input.Select_Data = hikari.Output.Data
        return hikari
    # 初次调用时或回调时有参数
    elif hikari.Input.Select_Index:
        # 初次调用时有选择序号，查询一次绑定列表
        if not hikari.Input.Select_Data:
            hikari.Status = 'init'
            hikari = await get_BindInfo(hikari)
            if not hikari.Status == 'success':
                return hikari
            hikari.Input.Select_Data = hikari.Output.Data
        if hikari.Input.Select_Index > len(hikari.Input.Select_Data):
            return hikari.error('请选择正确的序号')
        hikari.Input.AccountId = hikari.Input.Select_Data[hikari.Input.Select_Index - 1]['accountId']
        url = f'{hikari_config.yuyuko_url}/api/user/platform/remove/bind'
        params = {'platformType': hikari.Input.Platform, 'platformId': hikari.Input.PlatformId, 'accountId': hikari.Input.AccountId}
        client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
        resp = await client_yuyuko.request('DELETE', url=url, json=params, timeout=20)
        result = json.loads(resp.content)
        if result['code'] == 200 and result['message'] == 'success':
            return hikari.success(
                f"删除绑定成功，删除的账号为{hikari.Input.Select_Data[hikari.Input.Select_Index - 1]['server']}：{hikari.Input.Select_Data[hikari.Input.Select_Index - 1]['userName']}"
            )
        else:
            return hikari.failed(f"{result['message']}")


@handle_yuyuko_errors()
async def get_DefaultBindInfo(hikari: Hikari_Model, platformType, platformId):
    """获取默认绑定账号
     Args:
        platformType (str):平台类型
        platformId (str):平台ID
    Returns:
        Dict:绑定用户信息
    """

    url = f'{hikari_config.yuyuko_url}/api/user/platform/bind/list'
    params = {
        'platformType': platformType,
        'platformId': platformId,
    }
    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.get(url, params=params, timeout=20)
    result = json.loads(resp.content)
    if result['code'] == 200 and result['message'] == 'success':
        if result['data']:
            for each in result['data']:
                if each['defaultId']:
                    return each
        else:
            return None


@handle_yuyuko_errors()
async def update_user_cache(hikari: Hikari_Model) -> Hikari_Model:
    """更新用户缓存（wws me update 或 wws 服务器 游戏昵称 update）。

    提交格式参考 platform/bind/list：以 platformType + platformId 标识平台用户，
    并携带目标账号 server + accountId。
    me 模式更新默认绑定账号；服务器+昵称模式按昵称解析后更新指定账号。
    """
    if hikari.Status != 'init':
        return hikari.error('当前请求状态错误')

    # 解析平台目标
    platform_info = await _resolve_platform_target(hikari)
    if isinstance(platform_info, str):
        # 解析失败时 _resolve_platform_target 返回的是错误信息字符串，不能直接解包
        return hikari.error(platform_info)
    platform_type, platform_id = platform_info
    # 发送更新请求
    url = f'{hikari_config.yuyuko_url}/api/user/platform/cache/update'
    params = {
        'platformType': platform_type,
        'platformId': platform_id
    }

    client_yuyuko = await get_client_yuyuko(hikari.UserInfo)
    resp = await client_yuyuko.get(url, params=params, timeout=20)
    result = json.loads(resp.content)

    if result['code'] == 200 and result['message'] == 'success':
        return hikari.success('已更新用户缓存')
    else:
        return hikari.failed(f"{result['message']}")


async def _resolve_platform_target(hikari: Hikari_Model):
    """
    解析平台目标，返回 (platform_type, platform_id) 或错误信息字符串。

    Returns:
        tuple: (platform_type, platform_id) 成功时
        str: 错误信息失败时
    """
    # me 模式：默认绑定账号
    if hikari.Input.Search_Type == 1:
        return hikari.Input.Platform, hikari.Input.PlatformId

    # 服务器+昵称模式
    if not hikari.Input.Server or not hikari.Input.AccountName:
        return '请使用 wws me update 或 wws 服务器 游戏昵称 update'

    account_id = await get_AccountIdByName(
        hikari,
        hikari.Input.Server,
        hikari.Input.AccountName
    )

    if account_id is None:
        # 失败时状态已落在 hikari 上（failed 的消息在 Output.Data）；不要把这个返回值塞进
        # f-string —— 查不到账号时它过去返回的是 hikari 自己，模型自引用会直接 RecursionError
        return str(hikari.Output.Data) if hikari.Status == 'failed' else '查询账号失败，请稍后重试'

    return hikari.Input.Server, account_id