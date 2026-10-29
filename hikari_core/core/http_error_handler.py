"""统一的 yuyuko HTTP 请求异常处理装饰器。

将所有重复的 try/except (TimeoutError/ConnectTimeout → PoolTimeout → Exception)
模式集中到一处，通过装饰器参数区分不同的 recreate_client 函数。
"""

import inspect
import traceback
from asyncio.exceptions import TimeoutError
from functools import wraps
from typing import Any, Callable, List, Optional, Union

from httpx import ConnectTimeout, PoolTimeout
from loguru import logger

from .http_client import (
    recreate_client_default,
    recreate_client_wg,
    recreate_client_yuyuko,
)
from .model import Hikari_Model

# 字符串名 → recreate 函数的映射
_RECREATE_REGISTRY: dict[str, Callable] = {
    "yuyuko": recreate_client_yuyuko,
    "default": recreate_client_default,
    "wg": recreate_client_wg,
}


def _get_hikari(func: Callable, args: tuple, kwargs: dict) -> Optional[Hikari_Model]:
    """从函数参数中提取 Hikari_Model 实例。"""
    # 最快路径：第一个位置参数
    if args and isinstance(args[0], Hikari_Model):
        return args[0]
    # 命名参数
    if "hikari" in kwargs and isinstance(kwargs["hikari"], Hikari_Model):
        return kwargs["hikari"]
    # 兜底：签名检查
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        for value in bound.arguments.values():
            if isinstance(value, Hikari_Model):
                return value
    except (TypeError, ValueError):
        pass
    return None


def handle_yuyuko_errors(
    recreate_func: Union[str, Callable, List[Union[str, Callable]]] = "yuyuko",
    timeout_message: str = "请求超时了，请过会儿再尝试哦~",
    pool_timeout_message: str = "连接池异常，请尝试重新查询~",
    exception_message: str = "wuwuwu出了点问题，请联系麻麻解决",
    include_exception: bool = True,
) -> Callable:
    """为 yuyuko HTTP 请求函数添加统一的异常处理。

    用法::

        @handle_yuyuko_errors()
        async def get_ShipInfo(hikari: Hikari_Model) -> Hikari_Model:
            ...

        @handle_yuyuko_errors(recreate_func="default")
        async def get_help(hikari: Hikari_Model) -> Hikari_Model:
            ...

        @handle_yuyuko_errors(recreate_func=["default", "wg"])
        async def get_latest_info(server, account_id):
            ...

    Args:
        recreate_func: 发生 PoolTimeout 时要重建的客户端连接池。
            可传字符串 ``"yuyuko"`` / ``"default"`` / ``"wg"``，
            一个可调用对象，或它们的列表（重建多个客户端）。
        timeout_message: TimeoutError / ConnectTimeout 时的错误消息。
        pool_timeout_message: PoolTimeout 时的错误消息。
        exception_message: 其他异常时的错误消息前缀。
        include_exception: 是否在消息后追加异常信息。

    Returns:
        装饰后的异步函数。
    """
    # 统一为列表
    raw: list = [recreate_func] if not isinstance(recreate_func, list) else recreate_func
    recreate_funcs: list[Callable] = []
    for item in raw:
        if isinstance(item, str):
            fn = _RECREATE_REGISTRY.get(item)
            if fn is None:
                raise ValueError(
                    f"未知的客户端 '{item}'，可选: {list(_RECREATE_REGISTRY)}"
                )
            recreate_funcs.append(fn)
        elif callable(item):
            recreate_funcs.append(item)
        else:
            raise TypeError(f"recreate_func 须为 str 或 callable，收到 {type(item)}")

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except (TimeoutError, ConnectTimeout):
                logger.warning(traceback.format_exc())
                hikari = _get_hikari(func, args, kwargs)
                if hikari is not None:
                    return hikari.error(timeout_message)
                raise
            except PoolTimeout:
                for recreate in recreate_funcs:
                    await recreate()
                logger.info("连接池已重新创建")
                hikari = _get_hikari(func, args, kwargs)
                if hikari is not None:
                    return hikari.error(pool_timeout_message)
                raise
            except Exception as e:
                logger.error(traceback.format_exc())
                hikari = _get_hikari(func, args, kwargs)
                if hikari is not None:
                    msg = (
                        f"{exception_message}\n{e}"
                        if include_exception
                        else exception_message
                    )
                    return hikari.error(msg)
                raise

        return wrapper

    return decorator
