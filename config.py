"""MaiBot 侧配置模型（对应上游 AstrBot 的 _conf_schema.json）。

配置分组：
- plugin:  插件总开关与 schema 版本（MaiBot 要求存在 [plugin] 节）
- trigger: 唤起方式（指令词、/ 前缀、@ 机器人）
- wows:    yuyuko API / 渲染 / 网络参数
"""

from __future__ import annotations

from typing import ClassVar, Literal

from maibot_sdk import Field, PluginConfigBase

CONFIG_SCHEMA_VERSION = "1.4.0"


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


class TriggerConfig(PluginConfigBase):
    """唤起方式：用哪个指令词、要不要 / 前缀、群里要不要 @ 机器人。"""

    __ui_label__: ClassVar[str] = "唤起方式"
    __ui_icon__: ClassVar[str] = "campaign"
    __ui_order__: ClassVar[int] = 1

    command_aliases: str = Field(
        default="wws",
        description=(
            "可用的指令词，逗号分隔；内置可选：wws、水表、战绩、战舰、战舰世界。"
            "只有这里列出的词才会触发指令，其余写法交回聊天处理"
        ),
        json_schema_extra={"label": "指令词（逗号分隔）", "order": 0},
    )
    require_prefix: bool = Field(
        default=False,
        description=(
            "开启后群聊里必须带 /（或 !）前缀、或者 @ 机器人，裸写「wws …」不再触发；"
            "私聊不受限制"
        ),
        json_schema_extra={"label": "必须带 / 前缀或 @ 机器人", "order": 1},
    )
    require_at: bool = Field(
        default=False,
        description=(
            "开启后群聊里必须 @ 机器人（或写出机器人的昵称）才触发，/wws 也一样；"
            "私聊不受限制。适合只想在点名请求时才响应的群"
        ),
        json_schema_extra={"label": "群里必须 @ 机器人", "order": 2},
    )


class WowsConfig(PluginConfigBase):
    """yuyuko API 与渲染参数。"""

    __ui_label__: ClassVar[str] = "战绩查询与渲染"
    __ui_order__: ClassVar[int] = 2

    wows_token: str = Field(
        default="",
        description="yuyuko API token，格式为「QQ号:token」，必须自己申请（README「获取并填写 Token」一节）；留空时除帮助外的指令都会被拦截并提示获取方式",
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


class LlmToolConfig(PluginConfigBase):
    """AI 对话查询（LLM 工具）开关。"""

    __ui_label__: ClassVar[str] = "AI 对话查询（实验性）"
    __ui_icon__: ClassVar[str] = "smart_toy"
    __ui_order__: ClassVar[int] = 3

    enabled: bool = Field(
        default=False,
        description=(
            "实验性功能，默认关闭。开启后允许 AI 在聊天中调用战绩工具"
            "（关闭时只能走 /wws 指令）。AI 可能误触发或乱编数据，建议先小范围试"
        ),
        json_schema_extra={"label": "启用 AI 对话查询（实验性）", "order": 0},
    )
    evaluate: bool = Field(
        default=True,
        description="开启后 AI 拿到数据会点评玩家表现（水平/风格/亮点），关闭则只转述数据",
        json_schema_extra={"label": "AI 点评战绩", "order": 1},
    )
    rate_limit: int = Field(
        default=10,
        description="同一会话 60 秒内允许的工具调用次数，<=0 表示不限制",
        json_schema_extra={"label": "每分钟调用上限", "order": 2},
    )
    require_at: bool = Field(
        default=True,
        description=(
            "群聊里只有当消息 @ 了机器人才允许 AI 查询战绩，避免群友聊天时被误触发；"
            "私聊不受此限制（私聊本身就是对机器人说的）"
        ),
        json_schema_extra={"label": "仅 @ 我时可用", "order": 3},
    )
    enable_account: bool = Field(
        default=True,
        description="查询玩家战绩总表（总体水表）",
        json_schema_extra={"label": "总表查询", "order": 10},
    )
    enable_ship: bool = Field(
        default=True,
        description="查询指定单船的战绩",
        json_schema_extra={"label": "单船战绩", "order": 11},
    )
    enable_recent: bool = Field(
        default=True,
        description="查询最近战绩汇总（全部模式）",
        json_schema_extra={"label": "近期战绩", "order": 12},
    )
    enable_recent_random: bool = Field(
        default=True,
        description="查询最近随机战战绩",
        json_schema_extra={"label": "近期随机战", "order": 13},
    )
    enable_recent_rank: bool = Field(
        default=True,
        description="查询最近排位战战绩",
        json_schema_extra={"label": "近期排位战", "order": 14},
    )
    enable_ship_recent: bool = Field(
        default=True,
        description="查询指定单船的近期战绩",
        json_schema_extra={"label": "单船近期战绩", "order": 15},
    )
    enable_recent_battles: bool = Field(
        default=True,
        description="查询近期逐场战斗明细",
        json_schema_extra={"label": "逐场明细", "order": 16},
    )
    enable_ships: bool = Field(
        default=True,
        description="按等级/国家/舰种筛选战舰战绩列表",
        json_schema_extra={"label": "筛选查询", "order": 17},
    )
    enable_roll_ship: bool = Field(
        default=True,
        description="随机抽一条战舰（娱乐功能）",
        json_schema_extra={"label": "随机抽船", "order": 18},
    )
    enable_bind: bool = Field(
        default=True,
        description="绑定游戏账号",
        json_schema_extra={"label": "绑定账号", "order": 19},
    )
    enable_bind_list: bool = Field(
        default=True,
        description="查看已绑定的游戏账号",
        json_schema_extra={"label": "查看绑定", "order": 20},
    )
    enable_bind_change: bool = Field(
        default=True,
        description="切换当前使用的绑定账号",
        json_schema_extra={"label": "切换绑定", "order": 21},
    )


class MentionQueryConfig(PluginConfigBase):
    """群聊里 @ 群友查 TA 的水表（默认还要求同时叫到机器人，避免误触发）。"""

    __ui_label__: ClassVar[str] = "群聊 @ 查询（实验性）"
    __ui_icon__: ClassVar[str] = "alternate_email"
    __ui_order__: ClassVar[int] = 4

    enabled: bool = Field(
        default=False,
        description=(
            "实验性功能，默认关闭。开启后群里 @ 某位群友并提到水表/战绩时，"
            "直接查 TA 绑定账号的总表并发图，不需要 @ 机器人"
        ),
        json_schema_extra={"label": "启用 @ 查询（实验性）", "order": 0},
    )
    require_keyword: bool = Field(
        default=True,
        description=(
            "开启时消息里必须带上下面的关键词才触发；关闭后只要 @ 群友就直接查，"
            "群里随便 @ 一下都会甩水表，容易刷屏，慎用"
        ),
        json_schema_extra={"label": "需要关键词", "order": 1},
    )
    keywords: str = Field(
        default="水表,战绩,查一下,查查,查询",
        description="触发关键词，逗号分隔（中英文逗号都行）；只在「需要关键词」开启时生效",
        json_schema_extra={"label": "触发关键词", "order": 2},
    )
    rate_limit: int = Field(
        default=3,
        description="同一个群 60 秒内最多触发几次，<=0 表示不限制",
        json_schema_extra={"label": "每群每分钟上限", "order": 3},
    )
    require_bot_ref: bool = Field(
        default=True,
        description=(
            "开启时 @ 群友查水表还必须带上机器人：@ 机器人，或在文字里写出机器人在本群的"
            "昵称。昵称与别名直接读 MaiBot 的 bot_config.toml（[bot] nickname / alias_names），"
            "这里不用另外填。防止聊天里随便 @ 一下就误触发查询"
        ),
        json_schema_extra={"label": "需要 @ 机器人或叫机器人名字", "order": 4},
    )
    llm_unbound_reply: bool = Field(
        default=False,
        description=(
            "实验性功能，默认关闭。被 @ 的人没绑定时，用宿主模型按人格现写一句提示"
            "（多一次 LLM 调用，模型看不到聊天上下文）；关闭时用插件内置的固定文案"
        ),
        json_schema_extra={"label": "未绑定提示交给模型（实验性）", "order": 5},
    )


class WowsSettings(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    wows: WowsConfig = Field(default_factory=WowsConfig)
    llm_tool: LlmToolConfig = Field(default_factory=LlmToolConfig)
    mention_query: MentionQueryConfig = Field(default_factory=MentionQueryConfig)
