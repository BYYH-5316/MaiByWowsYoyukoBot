"""战舰世界战绩查询插件（MaiBot 接入层 / 唯一入口）。

- 上游原版：AstrBot 插件 AstrByWowsYuyukoBot，作者 西行寺雨季
  https://github.com/JustOneSummer/AstrByWowsYuyukoBot
- 渲染核心：hikari-core v2.0.1，作者 Hikari / benx1n（MIT）
  https://github.com/benx1n/HikariBot
- 战绩数据：yuyuko API https://v3-api.wows.shinoaki.com
- MaiBot 移植与维护：白云鱼海

指令：

- `/wws <参数>`      查询并渲染水表图片（帮助、绑定、单船、近期等全部子指令）
- 纯数字回复          消费 `/wws` 交互流程里「等用户回复序号」的那一步

渲染与数据都在 `hikari_core`（随插件分发）；本文件只做指令适配与生命周期管理。
"""

from __future__ import annotations

import re
from typing import Any

from maibot_sdk import Command, MaiBotPlugin

# 先导入 wows_bot：它的 __init__ 会把插件根目录挂进 sys.path，
# 之后才能把随包分发的 hikari_core 当顶级包导入。
from .config import WowsSettings
from .wows_bot import (
    CoreRuntime,
    OutputSender,
    SelectionManager,
    account_not_found_hint,
    missing_token_hint,
    reply_text,
)
from .wows_bot.log import logger
from hikari_core import callback_hikari, init_hikari

# yuyuko API 的 platformType（与聊天平台无关，固定 QQ）
PLATFORM = "QQ"

# 不需要 token 就能用的指令：空参数（等同 help）与帮助本身
_TOKEN_FREE_COMMANDS = {"", "help", "帮助"}

# 允许「@机器人 /wws xxx」「/wws xxx」「wws xxx」三种写法
WWW_PATTERN = r"^\s*(?:@\S+\s+)*[/!！／]?\s*wws(?:\s+(?P<rest>.*))?$"
SELECT_PATTERN = r"^\s*(?P<index>\d{1,2})\s*$"

_WWW_RE = re.compile(WWW_PATTERN)
_SELECT_RE = re.compile(SELECT_PATTERN)


class WowsYuyukoPlugin(MaiBotPlugin):
    """战舰世界战绩查询插件。"""

    config_model = WowsSettings

    def __init__(self) -> None:
        super().__init__()
        self.selection = SelectionManager()
        self.sender = OutputSender()
        self._runtime: CoreRuntime | None = None

    @property
    def runtime(self) -> CoreRuntime:
        if self._runtime is None:
            raise RuntimeError("插件尚未完成初始化")
        return self._runtime

    # ── 生命周期 ──────────────────────────────────────────────────

    async def on_load(self) -> None:
        """下发配置并启动 hikari-core。"""
        if not self._token_configured():
            logger.warning(
                "[wows] 未配置 yuyuko API Token：除 /wws help 外的指令都会提示先配置；"
                "获取方式见 README 8.3 节"
            )
        self._runtime = CoreRuntime(self.config, self.ctx.paths.data_dir)
        await self._runtime.start()

    async def on_unload(self) -> None:
        """插件被禁用/重载时清理临时资源。"""
        try:
            logger.info("[wows] 开始销毁临时资源")
            self.selection.clear()
            if self._runtime is not None:
                await self._runtime.shutdown()
            logger.info("[wows] 临时资源销毁完成")
        except Exception as e:
            logger.exception(f"[wows] 清理临时资源失败: {e}")

    async def on_config_update(
        self, scope: str, config_data: dict[str, Any], version: str
    ) -> None:
        """热更新插件自身配置（重新下发 hikari-core 配置）。"""
        del config_data, version
        if scope != "self" or self._runtime is None:
            return
        await self._runtime.apply_config(self.config)

    # ── 指令 ──────────────────────────────────────────────────────

    @Command(
        name="wws",
        description="战舰世界战绩查询（/wws help 查看用法）",
        pattern=WWW_PATTERN,
    )
    async def handle_wws(self, **kwargs: Any) -> tuple[bool, str, int]:
        """查询并渲染战绩图片。"""
        stream_id = str(kwargs.get("stream_id") or "")
        if not stream_id:
            return False, "", 0

        rest = self._wws_args(kwargs)
        # MaiBot 只记录「命令执行成功: wws」不带参数，出问题时全靠这行还原用户输入
        logger.info(
            f"[wows] 指令 {rest!r} user={kwargs.get('user_id')} group={kwargs.get('group_id')}"
        )
        if not self._token_configured() and not self._is_token_free(rest):
            logger.warning("[wows] 未配置 wows_token，已拦截指令并提示获取方式")
            await reply_text(self.ctx, stream_id, missing_token_hint())
            return True, "", 2

        if not self.runtime.is_ready():
            # 首次启动要下模板/资源，这期间别让指令静默卡死
            await reply_text(self.ctx, stream_id, "正在准备渲染资源，请稍等一会儿再查询哦~")
            return True, "", 2

        if self.selection.is_pending(stream_id):
            await reply_text(self.ctx, stream_id, "上一条查询还在等你回复序号，请先选完或等待超时~")
            return True, "", 2

        async with self.runtime.render_lock:
            try:
                hikari = await init_hikari(
                    command_text=rest,
                    platform=PLATFORM,
                    PlatformId=str(kwargs.get("user_id") or ""),
                    BotId=self._self_id(kwargs),
                    GroupId=str(kwargs.get("group_id") or "") or None,
                )
                if hikari.Status == "wait":
                    # 先登记状态槽再发选择界面，免得用户手快先回序号时状态还没就绪
                    state = self.selection.register(stream_id, hikari.Input.Select_Data)
                    await self.sender.send(self.ctx, stream_id, hikari)
                    index = await self.selection.wait(stream_id, state)
                    if index is None:
                        hikari = hikari.error("已超时退出")
                    else:
                        hikari.Input.Select_Index = index
                        hikari = await callback_hikari(hikari)
                logger.info(
                    f"[wows] 结果 status={hikari.Status} server={hikari.Input.Server} "
                    f"昵称={hikari.Input.AccountName!r} aid={hikari.Input.AccountId}"
                )
                await self._send_result(stream_id, hikari)
            except Exception as e:
                logger.exception(f"[wows] 指令处理异常: {e}")
                await reply_text(
                    self.ctx,
                    stream_id,
                    "呜呜呜发生了错误，可能是网络问题，如果过段时间不能恢复请联系麻麻哦~",
                )
        return True, "", 2

    @Command(
        name="wws_select",
        description="选择查询结果序号（/wws 交互流程内部使用）",
        pattern=SELECT_PATTERN,
    )
    async def handle_select(self, **kwargs: Any) -> tuple[bool, str, int]:
        """消费选择流程中用户回复的序号；没有待选择状态时放行给后续流程。"""
        stream_id = str(kwargs.get("stream_id") or "")
        if not stream_id or not self.selection.is_pending(stream_id):
            return False, "", 0

        async def reply(text: str) -> None:
            await reply_text(self.ctx, stream_id, text)

        consumed = await self.selection.consume_reply(
            stream_id, str(kwargs.get("text") or ""), reply
        )
        if consumed:
            return True, "", 2
        return False, "", 0

    # ── 结果发送 ──────────────────────────────────────────────────

    async def _send_result(self, stream_id: str, hikari) -> None:
        """发送渲染结果；昵称查不到时换成能自我纠错的提示。"""
        hint = account_not_found_hint(hikari)
        if hint:
            logger.info(f"[wows] 上游返回 {hikari.Output.Data!r}，改用排错提示")
            await reply_text(self.ctx, stream_id, hint)
            return
        await self.sender.send(self.ctx, stream_id, hikari)

    # ── 参数提取 ──────────────────────────────────────────────────

    @staticmethod
    def _wws_args(kwargs: dict[str, Any]) -> str:
        """取 `/wws` 之后的参数文本。"""
        groups = kwargs.get("matched_groups")
        if isinstance(groups, dict) and groups.get("rest") is not None:
            return str(groups["rest"]).strip()
        m = _WWW_RE.match(str(kwargs.get("text") or ""))
        return (m.group("rest") or "").strip() if m else ""

    @staticmethod
    def _self_id(kwargs: dict[str, Any]) -> str:
        """当前机器人账号（消息的接收账号）。"""
        message = kwargs.get("message")
        if isinstance(message, dict):
            return str(message.get("account_id") or "")
        return ""

    def _token_configured(self) -> bool:
        """是否已填写 yuyuko API Token（空串/纯空白视为未配置）。"""
        return bool((self.config.wows.wows_token or "").strip())

    @staticmethod
    def _is_token_free(rest: str) -> bool:
        """该指令是否无需 token（帮助页）。"""
        parts = rest.split(maxsplit=1)
        return (parts[0].lower() if parts else "") in _TOKEN_FREE_COMMANDS


def create_plugin() -> MaiBotPlugin:
    """MaiBot 插件工厂函数。"""
    return WowsYuyukoPlugin()
