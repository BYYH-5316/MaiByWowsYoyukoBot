"""「等用户回复序号」的选择流程。

一个会话（`event.unified_msg_origin`，即 `platform:message_type:session_id`）在同一时刻
只允许有一次选择流程，同一用户在不同群 / 私聊里的选择互不干扰。

这里不依赖 AstrBot 的事件对象，回复文案由调用方以 `reply(text)` 回调传入，
方便单独测试。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

# 等用户回复序号的超时（秒）
SELECT_TIMEOUT = 20.0

# reply 回调：给用户发一条纯文本
ReplyFunc = Callable[[str], Awaitable[None]]


@dataclass
class SelectState:
    """一次选择流程的共享状态。

    `SelectState` 与 `register` / `wait` 之间靠 `waiter` 同步：回复线程（事件循环里
    的另一个任务）填入 `index` 后 `set()`，等待方 `await waiter.wait()` 即可醒来，
    不需要轮询。
    """

    options: list
    waiter: asyncio.Event = field(default_factory=asyncio.Event)
    index: int | None = None


class SelectionManager:
    """按会话管理待选择状态。"""

    def __init__(self):
        self._states: dict[str, SelectState] = {}

    def is_pending(self, session: str) -> bool:
        """该会话是否已有一次等待中的选择。"""
        return session in self._states

    def register(self, session: str, options: list) -> SelectState:
        """登记一次选择流程（调用方保证该会话当前没有待选择状态）。"""
        state = SelectState(options=options or [])
        self._states[session] = state
        return state

    async def wait(self, session: str, state: SelectState) -> int | None:
        """等待用户回复一个合法序号；超时返回 None。必须在 `register` 之后调用。

        无论正常返回、超时还是被取消，都会清掉状态槽：留一个空状态会让字典
        随用过的会话无限增长，也会让该会话再也发不出新指令。
        """
        try:
            await asyncio.wait_for(state.waiter.wait(), timeout=SELECT_TIMEOUT)
            return state.index
        except asyncio.TimeoutError:
            return None
        finally:
            self._states.pop(session, None)

    async def consume_reply(self, session: str, text: str, reply: ReplyFunc) -> bool:
        """尝试把一条消息当作序号消费掉。

        Returns:
            bool: 该消息是否属于本模块处理（True = 调用方应当停止事件继续传播）。
        """
        state = self._states.get(session)
        if state is None:
            return False

        try:
            choice = int(text.strip())
        except ValueError:
            # 调用方通常已经用正则限定为纯数字，这里只做兜底
            return False
        if 1 <= choice <= len(state.options):
            state.index = choice
            state.waiter.set()
        else:
            await reply(f"请选择 1-{len(state.options)} 之间的序号")
        return True

    def clear(self):
        """丢弃全部状态（插件卸载时用）。"""
        self._states.clear()
