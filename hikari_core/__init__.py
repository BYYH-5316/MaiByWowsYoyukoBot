import os
import traceback
from typing import List

from loguru import logger
from playwright.async_api import Error as playwright_Error
from pydantic import ValidationError, Field

__version__ = '2.0.1'

from .core.cache_utils import get_cache_file
from .core.config import hikari_config, set_hikari_config  # noqa:F401 set_hikari_config为外部程序引用
from .core.constants import template_path
from .core import js_render
from .core.model import Hikari_Model, Input_Model, UserInfo_Model
# ba_text_em / server_cn 不再被服务端渲染用到（它们现在是浏览器端兼容层的真值来源，
# 由 js_render.compat_tables() 序列化下发）；这里保留为**公开再导出**，
# 免得下游 'from hikari_core import server_cn' 这类用法断掉。
from .core.render_helpers import ba_text_em, server_cn, set_render_params  # noqa: F401
from .core.user_image_cache import localize_user_images
from .Html_Render import BrowserRenderError, html_to_pic
from .commands.parser import analyze_command
# 供外部 bot 使用的公共指令 API（显式导出，替代通配导入）
from .commands.router import (  # noqa: F401
    command,
    first_command_list,
    select_command,
    get_AccountInfo,
    get_RecentInfo,
    get_RecentRandom,
    get_RecentRank,
    get_RecentsInfo,
    get_Ships,
    get_ShipInfo,
    get_ShipRecent,
    get_ShipRank,
    get_CwRank,
    get_cw_recent,
    get_ClanInfo,
    get_ClanRank,
    change_BindInfo,
    delete_BindInfo,
    get_BindInfo,
    set_BindInfo,
    set_special_BindInfo,
    update_user_cache,
    check_christmas_box,
    get_BanInfo,
    get_sx_info,
    roll_ship,
    get_ship_name,
    check_version,
    async_update_ship_cache,
    async_update_template,
    get_help,
)

# 服务端**没有**模板引擎了：模板由浏览器端的 Nunjucks 渲染（Template/hikari-render.js），
# Python 只负责把数据与模板源码打包成外壳 HTML（core/js_render.py）。
# 历史上这里有一个 jinja2.Environment，硬切换之后已删除 —— 迁移后的模板用了
# Nunjucks 专有写法（dget() / .push() / .slice()），Jinja 渲染不了它们。
logger.info(f'模板目录 as_uri: {template_path.as_uri()}')


async def init_hikari(
        platform: str,
        PlatformId: str,
        BotId: str,
        command_text: str = Field(default='', description='输入的指令'),
        GroupId: str = None,
        Ignore_List: List | None = None,  # noqa: B006
) -> Hikari_Model:
    """Hikari初始化

    Args:
        platform (str): 平台类型
        PlatformId (str): 平台ID
        command_text (str): 传入指令，不带wws
        GroupId (str): 群号,不配置无法使用部分分群功能
        Ignore_List(List):  禁用功能列表，通过import导入
    Returns:
        Hikari_Model: 可通过Hikari.Status和Hikari.Output.Data内数据判断是否输出
    """
    return await output_hikari(await init_hikari_no_output(platform, PlatformId, BotId, command_text, GroupId, Ignore_List))


async def init_hikari_no_output(
        platform: str,
        PlatformId: str,
        BotId: str,
        command_text: str = Field(default='', description='输入的指令'),
        GroupId: str | None = None,
        Ignore_List: List | None = None,  # noqa: B006
) -> Hikari_Model:
    try:
        if Ignore_List is None:
            Ignore_List = []
        hikari = Hikari_Model(UserInfo=UserInfo_Model(Platform=platform, PlatformId=PlatformId, BotId=BotId, GroupId=GroupId), Input=Input_Model(Command_Text=command_text))
        hikari = await analyze_command(hikari)
        if hikari.Status != 'init' or not hikari.Function:
            return hikari
        if hikari.Function in Ignore_List:
            return hikari.error('该功能已被禁用')
        hikari: Hikari_Model = await hikari.Function(hikari)
        return hikari
    except ValidationError:
        logger.error(traceback.format_exc())
        return Hikari_Model().error('参数校验错误，请联系开发者确认入参是否符合Model')
    except Exception:
        logger.error(traceback.format_exc())
        return Hikari_Model().error('Hikari-core顶层错误，请检查log')


async def callback_hikari(hikari: Hikari_Model) -> Hikari_Model:
    """回调wait状态的Hikari

    Args:
        hikari (Hikari_Model):前置或自行构造的Hikari_Model，可通过from hikari_core import Hikari_Model引入

    Returns:
        Hikari_Model: 可通过Hikari.Status和Hikari.Output.Data内数据判断是否输出
    """
    try:
        if hikari.Status != 'wait':
            return hikari.error('当前请求状态错误，请确认是否为wait')
        if not hikari.Function:
            return hikari.error('缺少请求方法')
        hikari: Hikari_Model = await hikari.Function(hikari)
        return await output_hikari(hikari)

    except Exception:
        logger.error(traceback.format_exc())
        return Hikari_Model().error('Hikari-core顶层错误，请检查log')


async def output_hikari(hikari: Hikari_Model) -> Hikari_Model:
    """输出Hikari

    Args:
        hikari (Hikari_Model):前置或自行构造的Hikari_Model，可通过from hikari_core import Hikari_Model引入

    Returns:
        Hikari_Model: 可通过Hikari.Status和Hikari.Output.Data内数据判断是否输出
    """
    try:
        if (
                hikari.Status in ['success', 'wait']
                and hikari_config.auto_rendering
                and hikari.Output.Template
                and (isinstance(hikari.Output.Data, dict) or isinstance(hikari.Output.Data, list))  # noqa: PLR1701
        ):
            # 获取全部的 shipInfo节点
            if hikari.Status == 'success':
                # 对 shipInfo节点进行修改 使用本地文件来渲染；头像等远程图片同样先落盘
                # （见 core/user_image_cache.py），别让浏览器去等几十个第三方 CDN 请求
                template_data = await set_render_params(
                    await localize_user_images(find_and_modify_shipinfo(hikari.Output.Data))
                )
            elif hikari.Status == 'wait':
                template_data = await set_render_params(
                    await localize_user_images(hikari.Input.Select_Data)
                )
            else:
                template_data = {}
            # 浏览器端渲染（**唯一路径**）：Python 只打包数据与模板源码，
            # 真正的渲染在浏览器里由 Template/hikari-render.js 用 Nunjucks 完成，
            # 截图服务会等 window.__hikari_render_done 再截图。
            # 见 tests/js_render_guard.py。
            render_root = template_data.get('template_path')
            content = js_render.render_shell(
                hikari.Output.Template,
                template_data.get('data'),
                root=render_root,
                template_path_uri=render_root.as_uri() if render_root else None,
            )
            # 测试模式下才赋值给模板内容
            if hikari_config.local_test:
                hikari.template_content = content
            # 渲染图片时额外保存一份 HTML，便于查看与调试
            if hikari_config.save_template_html:
                try:
                    html_dir = get_cache_file() / 'template_html'
                    html_dir.mkdir(parents=True, exist_ok=True)
                    html_path = html_dir / os.path.basename(hikari.Output.Template)
                    html_path.write_text(content, encoding='utf-8')
                    logger.info(f'已保存渲染HTML: {html_path}')
                except Exception:
                    logger.error(traceback.format_exc())
            hikari.Output.Data = content
            hikari.Output.Data_Type = str(type(hikari.Output.Data))
            if hikari_config.auto_image:
                hikari.Output.Data = await html_to_pic(
                    content,
                    wait=0,
                    viewport={'width': hikari.Output.Width, 'height': hikari.Output.Height},
                    use_browser=hikari_config.use_broswer,
                    type=hikari_config.image_type,
                )
                # 记录实际输出的图片格式（jpeg / png / webp），供接入端按格式发送
                hikari.Output.Data_Type = hikari_config.image_type
        return hikari
    except BrowserRenderError as e:
        # 只在 set_hikari_config(render_error_fallback=True) 时才会走到这里：
        # 浏览器端渲染没成功，与其给用户一张写着报错的图，不如回一条文本错误。
        logger.error(traceback.format_exc())
        return Hikari_Model().error(f'模板渲染错误（浏览器端），请将日志中的报错提交给开发者\n{e}')
    except playwright_Error as e:
        logger.error(traceback.format_exc())
        return Hikari_Model().error(f'playwright错误，请检查浏览器内核是否异常结束，可能是由于服务器版本过低，请升级至winserver2016+或改为firefox启动。\n{e}')
    except Exception as e:
        logger.error(traceback.format_exc())
        return Hikari_Model().error(f'Hikari-core顶层错误，请检查log\n{e}')


# shipInfo 中需要替换为本地缓存路径的图片字段
_SHIP_IMAGE_KEYS = ("shipTypeImage", "imgSmall", "countryImage")


def find_and_modify_shipinfo(data, target_key="shipInfo"):
    """深度搜索 shipInfo 节点，将远程图片 URL 替换为本地缓存路径。

    Args:
        data: 嵌套数据结构
        target_key: 要搜索的键名，默认 "shipInfo"
    """
    wows_temp = get_cache_file() / "ship_cache"

    def _replace_images(ship_info):
        """将 shipInfo 中的图片 URL 替换为本地 file:// 路径（若缓存文件存在）。"""
        for key in _SHIP_IMAGE_KEYS:
            if key in ship_info and ship_info[key] is not None:
                local = wows_temp / str(os.path.basename(ship_info[key]))
                if local.exists():
                    ship_info[key] = f"file:///{local.as_posix()}"

    def _recurse(obj):
        if isinstance(obj, dict):
            if target_key in obj and isinstance(obj[target_key], dict):
                _replace_images(obj[target_key])
            for value in obj.values():
                _recurse(value)
        elif isinstance(obj, list):
            for item in obj:
                _recurse(item)

    _recurse(data)
    return data
