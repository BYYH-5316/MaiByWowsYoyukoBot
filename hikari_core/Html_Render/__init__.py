from loguru import logger

from .browser import get_browser, get_new_page, shutdown_browser  # noqa: F401
from .data_source import html_to_pic  # noqa: F401
from .minimal_screens_hot_service import BrowserRenderError  # noqa: F401


async def init(**kwargs):
    """Start Browser

    Returns:
        Browser: Browser
    """
    browser = await get_browser(**kwargs)
    logger.info('Browser Started.')
    return browser


async def shutdown():
    await shutdown_browser()
    logger.info('Browser Stopped.')


browser_init = init

all = [
    'browser_init',
    'get_new_page',
    'html_to_pic',
]
