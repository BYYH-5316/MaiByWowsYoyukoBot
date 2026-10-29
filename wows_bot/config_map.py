"""MaiBot 插件配置 -> hikari-core 配置的映射。"""

from __future__ import annotations

from pathlib import Path

from ..config import WowsSettings


def build_hikari_config(settings: WowsSettings, data_dir: Path) -> dict:
    """把插件配置转成 `set_hikari_config(**kwargs)` 的入参。

    `game_path` 固定为插件数据目录：模板、战舰缓存、头像缓存、Playwright 内核
    都会落在它下面，卸载插件不会误删，重装也能复用。
    """
    c = settings.wows
    return {
        "proxy": (c.proxy_url or None) if c.proxy_enabled else None,
        "http2": c.http2_enabled,
        "token": c.wows_token,
        "use_broswer": c.use_browser,
        "image_type": c.image_type,
        "local_test": c.local_test,
        "save_template_html": c.save_template_html,
        "render_error_fallback": c.render_error_fallback,
        "user_image_cache_ttl_minutes": c.user_image_cache_ttl_minutes,
        "game_path": str(data_dir),
    }
