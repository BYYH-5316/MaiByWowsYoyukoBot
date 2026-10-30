"""把 hikari-core 的渲染结果发给用户。

v2 的 `Output.Data` 有三种形态：`bytes`（截图）、`str`（文本错误 / 提示）、
其它（未渲染的数据结构，兜底提示）。MaiBot 的 `ctx.send.image` 只收 base64，
所以这里不落盘，直接编码发送。
"""

from __future__ import annotations

import base64

from .log import logger

# GitHub 会把 README 的「### 8.3 获取 yuyuko API Token」渲染成这个锚点
TOKEN_GUIDE_URL = "https://github.com/BYYH-5316/MaiByWowsYoyukoBot#83-获取-yuyuko-api-token"


class OutputSender:
    """负责渲染结果的发送。"""

    async def send(self, ctx, stream_id: str, hikari_data) -> None:
        """按 `Output.Data` 的实际类型选择发送方式。"""
        data = hikari_data.Output.Data
        if isinstance(data, bytes):
            await self.send_image(ctx, stream_id, data)
        elif isinstance(data, str):
            await reply_text(ctx, stream_id, data)
        else:
            await reply_text(ctx, stream_id, f"未知数据类型标记 {hikari_data.Output.Data_Type}")

    async def send_image(self, ctx, stream_id: str, data: bytes) -> None:
        """把一段图片字节以 base64 直接发给当前会话。"""
        try:
            await ctx.send.image(base64.b64encode(data).decode("ascii"), stream_id)
        except Exception as e:
            logger.exception(f"[wows] 发送图片失败: {e}")
            await reply_text(ctx, stream_id, "图片发送失败了，请稍后再试哦~")


async def reply_text(ctx, stream_id: str, text: str) -> None:
    """给当前会话回一条纯文本。"""
    try:
        await ctx.send.text(text, stream_id)
    except Exception as e:
        logger.warning(f"[wows] 发送文本失败: {e}")


# yuyuko 的昵称搜索是**精确匹配**，查不到时上游只回「用户不存在」，
# 用户完全看不出是自己填错了服务器还是打错了昵称，这里把实际使用的参数回显出来。
SERVER_LABELS = {"cn": "国服", "asia": "亚服", "eu": "欧服", "na": "美服", "ru": "俄服"}


def missing_token_hint() -> str:
    """未配置 API Token 时回给用户的获取指引。"""
    return (
        "还没有配置 yuyuko API Token，先填好才能查询哦~\n"
        "1. 加 QQ 群 967546463，按群公告提交申请\n"
        "2. 通过后 token 会发到邮箱，格式为「QQ号:token」，原样复制\n"
        "3. 打开 WebUI → 插件页 → 战舰世界战绩查询 → 配置，把 token 填到「API Token」并保存\n"
        f"详细步骤见 README 第 8.3 节：{TOKEN_GUIDE_URL}"
    )


def account_not_found_hint(hikari) -> str | None:
    """昵称查不到时返回给用户的排错提示，其余情况返回 None（交回上游原文）。"""
    if getattr(hikari, "Status", "") != "failed":
        return None
    server = getattr(hikari.Input, "Server", None) or ""
    name = (getattr(hikari.Input, "AccountName", None) or "").strip()
    if not server or not name:
        return None
    label = SERVER_LABELS.get(server, server)
    return (
        f"在 {label}（{server}）没有找到昵称「{name}」哦~\n"
        f"1. 服务器对不对：国服 cn / 亚服 asia / 欧服 eu / 美服 na / 俄服 ru\n"
        f"2. 昵称要和游戏里一模一样，别带公会标签、别多空格\n"
        f"3. 昵称里有空格就整体加括号：/wws bind {server} (ABC DEF)\n"
        f"4. 还是查不到就改用 AID 绑定：/wws 特殊绑定 <AID>"
    )
