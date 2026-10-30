from pathlib import Path
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
from pydantic import BaseModel

_scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
# 启动时只执行一次
_initial_scheduler = True


class Config_Model(BaseModel):
    proxy: Optional[str] = None
    http2: bool = True
    token: Optional[str] = '123456:111111111111'
    Authorization: Optional[str] = None  # 自定义 Authorization（如 data_user 私有接口鉴权），为空时回退用 token
    auto_rendering: bool = True
    auto_image: bool = True
    image_type: str = 'jpeg'  # 截图输出格式: jpeg / png / webp
    use_broswer: Optional[str] = 'chromium'
    yuyuko_url: Optional[str] = 'https://v3-api.wows.shinoaki.com'
    yuyuko_type: Optional[str] = 'BOT'
    local_test: bool = False
    save_template_html: bool = False  # 渲染图片时额外保存一份 HTML，便于查看与调试
    # 浏览器端渲染失败时怎么办（模板写坏 / 渲染器资源缺失 / 超时）：
    #   False（缺省）= 照常截图 —— 用户会收到一张**写着报错的图**（hikari-render.js 把错误
    #                  渲染进页面），日志里有 '浏览器端渲染失败: error: …'；
    #   True         = 不截图，抛给上层 → output_hikari 回一条**文本错误**给用户。
    # 两种都能看见错误，区别是"图里带着"还是"文本回给用户"，按接入端偏好选。
    render_error_fallback: bool = False
    # ▍这里曾经有一个 js_rendering 开关（Jinja / 浏览器端 Nunjucks 二选一）。
    #   模板整体迁到 Nunjucks 之后它已经没有意义 —— 迁移后的模板 Jinja 渲染不了
    #   （dget() / .push() / .slice() 这些写法 Jinja 不认），所以开关被删掉了：
    #   渲染路径只有一条，见 core/js_render.py。
    # 指令输错时的智能提示配置
    command_suggest_max: int = 3  # 最大提示条数，设为 0 则关闭智能提示
    command_suggest_dedupe: bool = True  # 同一效果(相同功能)的多个别名只提示一条
    command_language: str = 'zh'  # 指令提示语言: zh=中文, en=英文（英文模式下提示英文指令）
    # 用户图片缓存（<缓存>/user-cache）的存活时间，**单位分钟**，默认 10080 = 7 天；
    # <= 0 表示永不过期。到期后下次渲染原地重下（QQ 头像那类 URL 恒定不变，
    # 不过期就会一直贴旧图）。
    user_image_cache_ttl_minutes: int = 10080


hikari_config = Config_Model()


def set_hikari_config(  # noqa: PLR0913
    proxy: Optional[str] = None,
    http2: bool = True,
    token: Optional[str] = '123456:111111111111',
    auto_rendering: bool = True,
    auto_image: bool = True,
    use_broswer: Optional[str] = 'chromium',
    game_path: Optional[str] = '',
    yuyuko_url: Optional[str] = 'https://v3-api.wows.shinoaki.com',
    yuyuko_type: Optional[str] = 'BOT',
    local_test: bool = False,
    save_template_html: bool = False,
    render_error_fallback: bool = False,
    command_suggest_max: int = 3,
    command_suggest_dedupe: bool = True,
    command_language: str = 'zh',
    image_type: str = 'jpeg',
    Authorization: Optional[str] = None,
    user_image_cache_ttl_minutes: int = 10080,
):
    """配置Hikari-core

    Args:
        proxy (str): 访问WG使用的代理，格式http://localhost:7890
        http2 (bool): 是否开启http2，默认启用
        token (str): #请加群联系雨季获取api_key和token Q群:967546463
        auto_rendering (bool): 自动填充模板，默认启用
        auto_image (bool): 是否自动渲染，默认启用，若auto_rending未启用则该项配置无效
        image_type (str): 截图输出图片格式 jpeg/png/webp，默认 jpeg（png/webp 经 Pillow 处理，质量略慢但无损/体积更小）
        use_broswer (str): chromium/firefox，默认chromium，性能大约为firefox三倍
        game_path (str):缓存文件夹路径，推荐设置在bot目录下，不配置默认为当前项目的data/wows-yuyuko目录下
        yuyuko_url (str):yuyuko请求地址
        command_suggest_max (int): 指令输错时最大提示条数，默认3，设为0则关闭智能提示
        command_suggest_dedupe (bool): 相同功能的多别名只提示一条，默认开启
        command_language (str): 指令提示语言，zh=中文(默认)，en=英文（提示英文指令与英文参数用法）
        save_template_html (bool): 渲染图片时额外保存一份 HTML 到缓存目录 template_html/，默认关闭
        render_error_fallback (bool): 浏览器端渲染失败（模板报错/资源缺失/超时）时是否改为回文本错误。
            默认 False = 照常截图（图里带着报错信息）；True = 不截图，抛给上层由 output_hikari 回文本错误
        Authorization (str): 自定义 Authorization（如 data_user 私有接口鉴权），为空时回退用 token
        user_image_cache_ttl_minutes (int): 用户头像/横幅等本地图片缓存的存活时间，**单位分钟**，
            默认 10080（7 天）；设 <= 0 则永不过期
    """
    global hikari_config  # noqa: PLW0602
    hikari_config.proxy = proxy
    hikari_config.http2 = http2
    hikari_config.token = token
    hikari_config.auto_rendering = auto_rendering
    hikari_config.auto_image = auto_image
    hikari_config.use_broswer = use_broswer
    hikari_config.yuyuko_url = yuyuko_url
    hikari_config.yuyuko_type = yuyuko_type
    hikari_config.local_test = local_test
    hikari_config.save_template_html = save_template_html
    hikari_config.render_error_fallback = render_error_fallback
    hikari_config.command_suggest_max = command_suggest_max
    hikari_config.command_suggest_dedupe = command_suggest_dedupe
    hikari_config.command_language = 'en' if str(command_language).lower().startswith('en') else 'zh'
    image_type = str(image_type).lower()
    if image_type not in ('jpeg', 'png', 'webp'):
        logger.warning(f'不支持的 image_type={image_type}，已回退为 jpeg')
        image_type = 'jpeg'
    hikari_config.image_type = image_type
    hikari_config.Authorization = Authorization
    try:
        hikari_config.user_image_cache_ttl_minutes = int(user_image_cache_ttl_minutes)
    except (TypeError, ValueError):
        logger.warning(f'user_image_cache_ttl_minutes={user_image_cache_ttl_minutes!r} 不是整数，回退为默认 10080 分钟（7 天）')
        hikari_config.user_image_cache_ttl_minutes = 10080
    # 为空则使用默认路径
    from hikari_core.core.cache_utils import get_cache_file, initial_cache_file

    try:
        if game_path == '':
            cache_dir = get_cache_file()
            initial_cache_file(cache_dir)
            logger.info(f'数据目录{cache_dir!s}')
        else:
            initial_cache_file(Path(game_path))
    except Exception as e:
        logger.error(f'初始化数据目录失败: {e}')
    logger.info(f'当前hikari-core配置\n{hikari_config}')
    global _initial_scheduler
    if _initial_scheduler and local_test is not True:
        logger.info('初始化任务....')
        from hikari_core.features.system import update_ship_cache, update_ship_cache_cron, update_template

        logger.info('执行初始模板更新...')
        update_template()
        logger.info('执行初始缓存更新...')
        update_ship_cache()
        logger.info('启动定时任务')
        _scheduler.add_job(update_template, 'cron', hour='4,12')
        _scheduler.add_job(update_ship_cache_cron, 'cron', day_of_week='thu', hour=8, minute=0)
        _scheduler.start()
        logger.info('初始化任务结束....')
        _initial_scheduler = False

    # ============================================================
    # 管理员校验串（启动时输出）
    # 流程：先把 admin.txt 中的管理员ID读入全局缓存；若不存在 admin.txt，
    #       生成 32 位随机串写入 checkAdmin.txt 并输出到控制台。
    #       用户把校验串直接私信发送给机器人（无需指令前缀）即可完成验证+写入。
    # SDK 接入端提示：建议引导用户通过私信发送校验串（避免群聊泄露），
    # 接入端需保证机器人收到的私信消息也进入 init_hikari 指令流程，详见 docs/admin-guide.md
    # ============================================================
    from hikari_core.core.admin import generate_check_admin, load_admin_cache

    load_admin_cache()
    token = generate_check_admin()
    if token:
        logger.info(f'管理员校验串已生成（请私信发送给机器人）：{token}')
