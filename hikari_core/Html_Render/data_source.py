from os import getcwd
from typing import Literal, Union

from .minimal_screens_hot_service import minimal_screens_hot_service


async def html_to_pic(  # noqa: PLR0913
        html: str,
        wait: int = 0,
        template_path: str = f'file://{getcwd()}',  # noqa: B008
        type: Union[Literal['jpeg', 'png', 'webp'], None] = None,
        quality: Union[int, None] = None,
        use_browser: str = 'chromium',
        **kwargs,
) -> bytes:
    """html转图片

    Args:
        html (str): html文本
        wait (int, optional): 等待时间. Defaults to 0.
        template_path (str, optional): 模板路径 如 "file:///path/to/template/"
        type (Literal["jpeg", "png", "webp"], optional): 图片类型，默认 None 等同 jpeg（与现行为一致）
        quality (int, optional): 图片质量 0-100，仅 jpeg/webp 生效，png 忽略
        **kwargs: 传入 page 的参数

    Returns:
        bytes: 图片, 可直接发送
    """
    # logger.debug(f"html:\n{html}")
    if 'file:' not in template_path:
        raise Exception('template_path 应该为 file:///path/to/template')
    hot_service = await minimal_screens_hot_service.get_instance()
    return await hot_service.screenshot(html_content=html, viewport=kwargs['viewport'], type=type, quality=quality)
