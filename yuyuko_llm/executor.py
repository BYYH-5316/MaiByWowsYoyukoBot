"""LLM 工具的共享执行流程。

每个工具在 ``plugin.py`` 里只是一个薄壳（``@Tool`` 的参数声明），
真正的流程在这里，避免 12 份重复代码：

    限流检查 -> 查询（不渲染） -> 渲染发图给用户 -> 返回精简数据给 LLM

▍关于「发图给用户 + 给 LLM 文字」
图片对模型没有价值，但用户喜欢现有的排版。所以两边各拿各的：
用户收到和 ``wws`` 指令一模一样的渲染图，模型收到提取后的字段用来点评。

▍关于故障话术
返回给模型的是**行动指引**而非原始报错：网络波动让它提示重试，
程序异常让它引导用户加群反馈（见 ``guide.classify_error``）。
原因是这两类对用户的意义完全不同 —— 程序异常重试多少次都没用，
笼统回一句「稍后重试」只会让用户白试，问题也永远暴露不出来。

▍与上游 AstrBot 版的差异
上游拿 ``AstrMessageEvent`` 取平台/发送者/会话 ID；MaiBot 的 ``@Tool`` 只保证
``stream_id`` 与 ``message``，所以这里改用 :class:`ToolContext` 承载已解析好的字段，
解析逻辑在 ``plugin.py``（拿不到就不注入身份，退化成让模型向用户要昵称）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..wows_bot.log import logger
from .guide import (
    ERROR_EXCEPTION,
    ERROR_NETWORK,
    ToolGuard,
    classify_error,
    config_get_bool,
    humanize_error,
)
from .query import QueryResult, QueryRunner, resolve_identity
from .tool_spec import IMAGE_FAILED_GUIDE, SERVER_CODE_HINT

__all__ = ["ToolExecutor", "ToolContext"]


# 模型侧文本的上限：数据本身不大，但异常情况下兜一下，避免把 prompt 撑爆
MAX_PAYLOAD_CHARS = 6000


@dataclass(frozen=True)
class ToolContext:
    """一次工具调用里 MaiBot 侧注入的上下文。"""

    ctx: Any
    stream_id: str
    user_id: str = ""
    group_id: str = ""
    bot_id: str = ""
    platform: str = "QQ"


class ToolExecutor:
    """把「一次工具调用」跑完：查数据、发图、组织给模型的返回值。"""

    def __init__(self, runtime, sender, guard: ToolGuard, config=None):
        """
        Args:
            runtime: ``wows_bot.runtime.CoreRuntime``，用来判断就绪与串行化渲染
            sender: ``wows_bot.sender.OutputSender``
            guard: 限流与开关
            config: 插件的 ``[llm_tool]`` 配置（点评开关等）
        """
        self._runtime = runtime
        self._sender = sender
        self._guard = guard
        self._config = config

    async def execute(
        self,
        tc: ToolContext,
        command_text: str,
        extractor: str,
        tool_name: str = "",
        *,
        send_image: bool = True,
        select_index: int | None = None,
        battle_mode: str | None = None,
        inject_identity: bool = False,
        require_user: bool = False,
        prefer_exact: str | None = None,
        **extract_kwargs: Any,
    ) -> str:
        """执行一次工具调用，返回给模型的文本。

        Args:
            tool_name: 工具名，用于查它自己的开关（见 ``guide.TOOL_SWITCH_FIELDS``）。
            require_user: 工具是否依赖当前用户的平台账号（如 roll 走 yuyuko 的
                平台绑定）；没有账号时给出绑定引导，而不是把上游的参数校验错误丢给模型。
            inject_identity: 是否需要「查自己」的账号上下文。
                为 True 时由插件解析绑定账号并注入，绕开 hikari me 模式的
                「平台 ID 当 accountId」问题（见 ``query.resolve_identity``）。
                查询类工具都要开；``roll`` 不需要账号，绑定类工具本身就是绑定操作，
                二者传 False 即可。
            prefer_exact: 船名搜索命中多条时自动选中同名那条（见
                ``query.match_exact_index``），单船类工具传用户写的船名即可。
        """
        # 1) 总开关：关掉时给出明确说明，免得模型以为自己用错了参数
        if not self._guard.enabled():
            return "战绩查询工具当前已被机器人管理员关闭，请告知用户暂时无法查询。"

        # 2) 单个工具的开关
        if tool_name and not self._guard.tool_enabled(tool_name):
            return f"工具 {tool_name} 已被机器人管理员单独关闭，请告知用户该功能暂不可用。"

        # 3) 就绪门控：首次启动要下模板与战舰资源
        if not self._runtime.is_ready():
            return "战绩查询功能正在初始化（首次使用需下载资源），请让用户稍等一会儿再试。"

        # 4) 会话校验：图片和回复都要落在这个会话里
        if not tc.stream_id:
            return "无法定位当前会话，请告诉用户稍后再试一次。"
        if not tc.platform:
            return "当前消息平台不支持战绩查询。"

        # 5) 依赖平台账号但拿不到：给出可执行的下一步，别把参数校验错误丢给模型
        if require_user and not tc.user_id:
            return (
                "无法确定当前用户的平台账号，这个功能需要用户先绑定游戏账号。"
                "请让用户完成绑定（可用 wws_bind 工具）后再重新调用本工具。"
            )

        # 6) 查自己但没有平台账号：不猜，直接让模型向用户要昵称
        if inject_identity and not tc.user_id:
            return (
                "无法确定当前用户的平台账号，因此查不了「TA 自己」的战绩。"
                "请向用户询问游戏昵称与服务器，然后带上 nickname 和 server 重新调用本工具。"
            )

        # 7) 限流
        limited = self._guard.check(tc.stream_id)
        if limited:
            return limited

        runner = QueryRunner(
            platform=tc.platform,
            platform_id=tc.user_id,
            bot_id=tc.bot_id,
            group_id=tc.group_id or None,
        )

        result = await runner.run(
            command_text,
            extractor,
            select_index=select_index,
            battle_mode=battle_mode,
            inject_identity=inject_identity,
            prefer_exact=prefer_exact,
            **extract_kwargs,
        )

        # 8) 需要用户二次选择：把候选列表交给模型转述，不发图
        if result.need_select:
            return result.message

        if not result.ok:
            return await self._format_error(result, tc, tool_name)

        # 9) 渲染发图：和 wws 指令走同一套渲染与发送逻辑
        image_failed = False
        if send_image:
            image_failed = await self._send_rendered(runner, result, tc)

        return self._format_payload(extractor, result, image_failed=image_failed)

    async def _send_rendered(
        self, runner: QueryRunner, result: QueryResult, tc: ToolContext
    ) -> bool:
        """渲染并发送图片。

        Returns:
            bool: 图片是否**本应发出去但失败了**。

            刻意区分「没有图片」和「发图失败」：
            ``Output.Data`` 不是 bytes 时（例如模板只回了字符串、或渲染产出了
            HTML 而非位图）属于**正常情况**，不该报错；只有真的尝试发送却出错，
            才算失败。否则会把正常路径误报成故障。
        """
        try:
            async with self._runtime.render_lock:
                data = await runner.render(result)
        except Exception as e:
            logger.exception(f"[wows] LLM 工具渲染图片失败: {e}")
            self._log_failure("渲染", e)
            return True

        if not data:
            # 本轮没有图片产物：不是故障，交给模型用文字作答即可
            logger.debug("[wows] LLM 工具本轮没有图片产物，按文本处理")
            return False

        try:
            await self._sender.send_image(tc.ctx, tc.stream_id, data)
            return False
        except Exception as e:
            logger.exception(f"[wows] LLM 工具发送图片失败: {e}")
            self._log_failure("发送", e)
            return True

    @staticmethod
    def _log_failure(stage: str, exc: BaseException) -> None:
        """按故障类型记不同级别，便于线上快速区分「我们的 bug」和「网络抖动」。"""
        if classify_error(exc) == ERROR_EXCEPTION:
            logger.error(f"[wows] LLM 工具{stage}图片失败（程序异常，需要排查）: {exc!r}")
        else:
            logger.warning(f"[wows] LLM 工具{stage}图片失败（网络类，可重试）: {exc!r}")

    async def _format_error(
        self, result: QueryResult, tc: ToolContext, tool_name: str = ""
    ) -> str:
        """失败信息交给话术层换成行动指引。

        ``network`` / ``exception`` 两类**不把原始报错交给模型** ——
        那些是给开发者看的堆栈与英文异常，转述给用户毫无意义还容易吓人。

        绑定失败时额外查一次已有绑定：用户其实绑过账号却反复让 TA 再绑，
        是最常见的死循环（模型看到「昵称不合法」就以为没绑）。
        """
        if result.error_kind in (ERROR_NETWORK, ERROR_EXCEPTION):
            return humanize_error("", result.error_kind)
        text = humanize_error(result.message, result.error_kind)
        if tool_name == "wws_bind" and tc.user_id:
            bound = await resolve_identity(tc.platform, tc.user_id)
            if bound and bound.get("user_name"):
                text += (
                    f"\n（补充：这个用户已经绑定过 {bound.get('server')} "
                    f"{bound.get('user_name')}，不需要再绑定。要查 TA 自己的战绩，"
                    "直接调用 wws_account / wws_recent 并留空 server 和 nickname。）"
                )
        return text

    @staticmethod
    def _log_extracted(extractor: str, payload: Any) -> None:
        """把提取结果打到日志，便于核对「到底提取到了什么」。

        排查时最有用的一条：抽取出来是空的（``EMPTY``）说明字段路径跟上游对不上，
        而不是上游没数据 —— 这两种情况从用户侧看到的都是「没有数据」。

        全程 try/except：日志绝不能因为它自己出错而影响业务；中文用
        ``ensure_ascii=False`` 保留可读性，失败再退回 ``str()``。
        """
        try:
            if isinstance(payload, str):
                summary = f"str({len(payload)}字)"
            elif isinstance(payload, dict):
                summary = f"dict, {len(payload)} 个键: {list(payload)[:12]}"
            elif isinstance(payload, list):
                summary = f"list, {len(payload)} 项"
            else:
                summary = type(payload).__name__

            # 判断「有没有真的提到东西」：空 dict/空列表/空串都算没提到
            empty = payload is None or payload == {} or payload == [] or payload == ""
            try:
                text = json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                text = str(payload)
            if len(text) > 4000:
                text = text[:4000] + f"…（共 {len(text)} 字符，已截断）"

            logger.info(
                f"[wows] [战绩提取] 类型={extractor} 结果={'EMPTY' if empty else summary}\n{text}"
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[wows] [战绩提取] 日志输出失败: {e!r}")

    def _format_payload(
        self,
        extractor: str,
        result: QueryResult,
        *,
        image_failed: bool = False,
    ) -> str:
        """把提取结果压成给模型的文本。"""
        payload = result.data
        self._log_extracted(extractor, payload)

        # 字符串结果（如「绑定成功」「切换绑定成功，当前绑定账号…」）：直接转达即可，
        # 不必包成 JSON，否则模型容易把简单消息读成结构化数据再啰嗦一遍
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return "操作已完成，但没有返回可读信息，请如实告知用户。"
            head = f"操作已成功，以下是系统返回的原话，请自然地向用户转述。{SERVER_CODE_HINT}\n"
            return head + self._clip(text, MAX_PAYLOAD_CHARS)

        if not payload:
            return "查询成功，但没有提取到可读数据（可能是该账号在该条件下没有记录），请如实告知用户。"

        try:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(payload)

        if image_failed:
            # 图片没发出去，只能靠模型用文字把结论讲清楚，所以数据要留够
            return (
                f"{IMAGE_FAILED_GUIDE}\n"
                f"（图片发送失败时，请直接用文字回答用户，不要提「数据」「JSON」这类字眼，"
                f"就像正常汇报战绩那样说话。另需向用户致歉说明图片没发出来。）\n"
                f"{self._clip(text, MAX_PAYLOAD_CHARS)}"
            )

        if len(text) > MAX_PAYLOAD_CHARS:
            text = text[:MAX_PAYLOAD_CHARS] + "…（数据过长已截断）"

        head = "以下是用户要查询的战绩数据（JSON），请据此作答"
        if self._evaluate_enabled():
            head += "，并用自然的语气点评一下玩家的表现（水平、风格、亮点或不足）"
        head += (
            "。图片已经发给用户了，不需要再把数据罗列一遍；"
            "但回复里只要提到数字，就必须和上面的数据完全一致，绝对不能凭空编造。\n\n"
        )
        return head + text

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit] + "…（已截断）"

    def _evaluate_enabled(self) -> bool:
        """评价开关；关掉时只让模型转述数据，不引导点评。"""
        return config_get_bool(self._config, "evaluate", True)
