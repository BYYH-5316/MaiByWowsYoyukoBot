"""MaiBot 侧配置模型（对应上游 AstrBot 的 _conf_schema.json）。

配置分组：
- plugin: 插件总开关与 schema 版本（MaiBot 要求存在 [plugin] 节）
- wows:   yuyuko API / 渲染 / 网络参数
"""

from __future__ import annotations

from typing import ClassVar, Literal

from maibot_sdk import Field, PluginConfigBase

CONFIG_SCHEMA_VERSION = "1.0.0"


class PluginSection(PluginConfigBase):
    """插件总开关。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_order__: ClassVar[int] = 0

    config_version: str = Field(
        default=CONFIG_SCHEMA_VERSION,
        description="配置 schema 版本，请勿手动修改。",
        json_schema_extra={
            "disabled": True,
            "hidden": True,
            "label": "配置版本",
            "order": 99,
        },
    )
    enabled: bool = Field(
        default=True,
        description="是否启用战舰世界战绩查询插件",
        json_schema_extra={"label": "启用插件", "order": 0},
    )


class WowsConfig(PluginConfigBase):
    """yuyuko API 与渲染参数。"""

    __ui_label__: ClassVar[str] = "战绩查询与渲染"
    __ui_order__: ClassVar[int] = 1

    wows_token: str = Field(
        default="",
        description="yuyuko API token，格式为「QQ号:token」，必须自己申请（README 8.3 节）；留空时除帮助外的指令都会被拦截并提示获取方式",
        json_schema_extra={"label": "API Token", "order": 0},
    )
    use_browser: Literal["chromium", "firefox"] = Field(
        default="chromium",
        description="截图内核：chromium 性能约为 firefox 三倍",
        json_schema_extra={"label": "浏览器内核", "order": 10},
    )
    image_type: Literal["jpeg", "png", "webp"] = Field(
        default="jpeg",
        description="截图输出格式；png/webp 体积更小但稍慢",
        json_schema_extra={"label": "图片格式", "order": 11},
    )
    user_image_cache_ttl_minutes: int = Field(
        default=10080,
        description="头像/横幅本地缓存存活时间（分钟），<=0 表示永不过期",
        json_schema_extra={"label": "头像缓存时长（分钟）", "order": 12},
    )
    proxy_enabled: bool = Field(
        default=False,
        description="是否启用代理",
        json_schema_extra={"label": "启用代理", "order": 20},
    )
    proxy_url: str = Field(
        default="http://127.0.0.1:7890",
        description="代理地址，仅在启用代理时生效",
        json_schema_extra={"label": "代理地址", "order": 21},
    )
    http2_enabled: bool = Field(
        default=True,
        description="是否开启 http2",
        json_schema_extra={"label": "开启 http2", "order": 22},
    )
    local_test: bool = Field(
        default=False,
        description="调试模式：额外落盘渲染用的 HTML",
        json_schema_extra={"label": "调试模式", "order": 30},
    )
    save_template_html: bool = Field(
        default=False,
        description="渲染图片时额外保存 HTML 到缓存目录 template_html/",
        json_schema_extra={"label": "保存渲染 HTML", "order": 31},
    )
    render_error_fallback: bool = Field(
        default=False,
        description="浏览器端渲染失败时改为回文本错误（关闭则把报错画进图片）",
        json_schema_extra={"label": "渲染失败回文本", "order": 32},
    )


class WowsSettings(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    wows: WowsConfig = Field(default_factory=WowsConfig)
