"""hikari-core 的启动、就绪判断与收尾。

`set_hikari_config` 不是协程，内部还要同步拉模板清单 + 战舰资源（HikariBot-Official
的 `start.py` 同样是模块级同步调用），所以放线程里跑，别堵 Runner 的事件循环。
"""

from __future__ import annotations

import asyncio
import shutil
import time
import zipfile
from pathlib import Path

from .config_map import build_hikari_config
from .log import logger

# 启动配置的上限：超时就不再干等，转由 `is_ready()` 按「未就绪」提示用户
STARTUP_TIMEOUT = 300.0

# hikari-core 的缓存目录下，本插件放渲染产物的子目录
TEMP_DIR_NAME = "file_img_temp"


class CoreRuntime:
    """持有 hikari-core 的启动任务，并负责它的就绪状态与资源回收。"""

    def __init__(self, settings, data_dir):
        self._settings = settings
        self._data_dir: Path = Path(data_dir)
        self._startup_task: asyncio.Task | None = None
        # 渲染串行化锁：hikari-core 的截图服务是**单例单浏览器**，
        # 并发会在首次惰性初始化上打架，重复拉起 playwright 与浏览器进程。
        # 惰性绑定事件循环：对象可能在还没有运行中 loop 的阶段被创建。
        self._render_lock: asyncio.Semaphore | None = None
        self._render_lock_loop: asyncio.AbstractEventLoop | None = None

    @property
    def render_lock(self) -> asyncio.Semaphore:
        """返回当前事件循环上的渲染信号量（同一循环内复用同一个）。"""
        loop = asyncio.get_running_loop()
        if self._render_lock is None or self._render_lock_loop is not loop:
            self._render_lock = asyncio.Semaphore(1)
            self._render_lock_loop = loop
        return self._render_lock

    async def start(self) -> None:
        """下发配置并启动 hikari-core（同步重活都在线程里，超时不阻塞事件循环）。"""
        logger.info("[wows] 开始初始化 wows-yuyuko")
        try:
            self._startup_task = asyncio.create_task(asyncio.to_thread(self._setup))
            await asyncio.wait_for(
                asyncio.shield(self._startup_task), timeout=STARTUP_TIMEOUT
            )
        except asyncio.TimeoutError:
            # shield 保证首次下载不会因为超时而半途被取消，后台继续跑完
            logger.warning(
                f"[wows] 初始化超过 {STARTUP_TIMEOUT:.0f}s 仍未完成"
                "（首次使用需下载模板与战舰资源），将在后台继续，就绪前会提示用户稍后再试"
            )
        except Exception as e:
            logger.exception(f"[wows] 初始化失败: {e}")

    async def apply_config(self, settings) -> None:
        """配置热更新：重新下发 hikari-core 配置（不重建截图服务）。"""
        self._settings = settings
        try:
            await asyncio.wait_for(asyncio.to_thread(self._setup), timeout=STARTUP_TIMEOUT)
            logger.info("[wows] 配置已重新下发")
        except asyncio.TimeoutError:
            logger.warning(f"[wows] 配置重新下发超过 {STARTUP_TIMEOUT:.0f}s，仍在后台执行")
        except Exception as e:
            logger.exception(f"[wows] 配置重新下发失败: {e}")

    def _setup(self) -> None:
        """在线程里执行 hikari-core 的同步启动配置。"""
        _ensure_ship_assets(self._data_dir)
        from hikari_core import set_hikari_config

        set_hikari_config(**build_hikari_config(self._settings, self._data_dir))
        # 配置热更新会重新导入插件模块，hikari 的模块级缓存目录可能已退回 cwd 兜底
        _ensure_cache_dir(self._data_dir)
        # set_hikari_config 内部已 initial_cache_file()，此时缓存目录才是配置后的那个
        get_temp_dir().mkdir(parents=True, exist_ok=True)
        _prepare_browser()

    def is_ready(self) -> bool:
        """hikari-core 是否已经就绪（配置下发完成且没抛异常）。"""
        task = self._startup_task
        if task is None or not task.done():
            return False
        return task.exception() is None

    @staticmethod
    async def shutdown() -> None:
        """停调度器、关浏览器，只清理截图产物（保留 browsers/ 与 ship_cache/）。"""
        _shutdown_scheduler()
        await _shutdown_browser()
        await clean_temp_dir()


def _ensure_ship_assets(cache_dir: Path) -> None:
    """修掉上次中断下载留下的半截 zip 与缺失清单，否则 hikari 启动时直接抛异常。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "ship_cache.zip"
    names: list[str] = []
    if zip_path.exists():
        try:
            with zipfile.ZipFile(zip_path) as z:
                names = [n for n in z.namelist() if not n.endswith("/")]
        except zipfile.BadZipFile:
            logger.warning("[wows] ship_cache.zip 不完整（下载被中断过），删除后重新下载")
            zip_path.unlink()
    if names:
        ship_dir = cache_dir / "ship_cache"
        extracted = (
            sum(1 for p in ship_dir.rglob("*") if p.is_file())
            if ship_dir.is_dir()
            else 0
        )
        if extracted < len(names):
            logger.warning(f"[wows] 战舰资源缺失（{extracted}/{len(names)}），重新解压")
            from hikari_core.features.system import extract_zip

            extract_zip(zip_path, ship_dir)
    if not (cache_dir / "ship_cache_hash.json").exists():
        from hikari_core.features.system import get_all_ship_cache_hash_json

        # 清单缺失时 hikari 的 update_ship_cache 会直接 open() 失败
        for _ in range(3):
            if get_all_ship_cache_hash_json(str(cache_dir)):
                return
            time.sleep(2)
        logger.warning("[wows] 战舰资源清单下载失败，初始化可能失败")


def _ensure_cache_dir(data_dir: Path) -> None:
    """把 hikari 的缓存目录钉回插件数据目录。

    配置热更新时插件模块被重新导入，`hikari_core.core.cache_utils` 的模块级
    `_project_base_dir` 会跟着重置；若不纠正，`setup_playwright` 会退回
    `cwd/data/wows-yuyuko` 并尝试重新下载浏览器内核。
    """
    from hikari_core.core import cache_utils

    base = cache_utils._project_base_dir
    if base is None or Path(base).resolve() != Path(data_dir).resolve():
        logger.warning(f"[wows] hikari 缓存目录异常（{base}），改回插件数据目录 {data_dir}")
        cache_utils.initial_cache_file(Path(data_dir))


def get_cache_dir() -> Path:
    """hikari-core 当前的缓存目录（必须在 `set_hikari_config` 之后读取才有意义）。"""
    from hikari_core import get_cache_file

    return get_cache_file()


def get_temp_dir() -> Path:
    """渲染产物的落盘目录。"""
    return get_cache_dir() / TEMP_DIR_NAME


async def clean_temp_dir() -> None:
    """删除本插件的截图落盘目录（不存在则跳过）；不动 browsers / ship_cache。"""
    temp_dir = get_temp_dir()
    if temp_dir.is_dir():
        await asyncio.to_thread(shutil.rmtree, temp_dir, True)


def _shutdown_scheduler() -> None:
    """停掉 hikari-core 的 APScheduler 定时任务。"""
    from hikari_core.core.config import _scheduler

    # local_test=True 时不会启动定时任务，此时 shutdown 会抛 SchedulerNotRunningError
    if _scheduler.running:
        _scheduler.shutdown()
        logger.info("[wows] hikari-core 定时任务已停止")


async def _shutdown_browser() -> None:
    """关掉 hikari-core 已启动的截图服务与浏览器进程（没启动过就跳过）。"""
    try:
        from hikari_core.Html_Render.minimal_screens_hot_service import (
            minimal_screens_hot_service,
        )

        service = minimal_screens_hot_service._instance
        if service is None:
            return
        await service.close()
        logger.info("[wows] hikari-core 截图服务已关闭")
    except Exception as e:
        logger.warning(f"[wows] 关闭截图服务失败: {e}")


def _prepare_browser() -> None:
    """初始化时下载好 Playwright 内核（同步阻塞，放在线程里跑）。"""
    from hikari_core.Html_Render.minimal_screens_hot_service import (
        minimal_screens_hot_service,
    )
    from hikari_core.core.config import hikari_config

    browser = hikari_config.use_broswer or "chromium"
    executable = minimal_screens_hot_service.setup_playwright(browser=browser)
    if not executable:
        raise RuntimeError(
            f"Playwright 浏览器内核（{browser}）准备失败，请检查网络/代理后重载插件"
        )
    logger.info(f"[wows] Playwright 浏览器内核已就绪: {executable}")
