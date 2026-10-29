"""管理员校验与添加。

流程（SDK 接入端提示：建议走私信，详细说明见 docs/admin-guide.md）：
1. 机器人启动成功后，控制台输出 32 位随机校验串
   （由 ``generate_check_admin`` 生成，写入 get_cache_file()/checkAdmin.txt）；
2. 用户把校验串**直接私信发送给机器人**（无需任何指令前缀），
   接入端需保证私信消息也进入 init_hikari 指令流程；
3. ``verify_and_add_admin`` 校验（``verify_check_admin``）通过后
   把发送者平台ID写入 get_cache_file()/admin.txt，删除 checkAdmin.txt，
   并同步到全局缓存 ``_ADMIN_IDS``；
4. 每次启动把 admin.txt 中的管理员ID读入全局缓存（``load_admin_cache``），
   校验入口先判断缓存，命中则不再走校验流程；
5. 再次启动时检测到 admin.txt 存在，则不再生成校验串。
"""

import secrets
import traceback
from pathlib import Path

from loguru import logger

from hikari_core.core.cache_utils import get_cache_file

ADMIN_FILE = 'admin.txt'
CHECK_FILE = 'checkAdmin.txt'
TOKEN_LENGTH = 32  # 校验串字符数（十六进制）

# 全局管理员缓存（启动时由 load_admin_cache 载入，添加管理员时同步更新）
_ADMIN_IDS: set = set()


def _admin_file() -> Path:
    return get_cache_file() / ADMIN_FILE


def _check_file() -> Path:
    return get_cache_file() / CHECK_FILE


def load_admin_cache() -> None:
    """启动时把 admin.txt 中的管理员ID读入全局缓存。"""
    global _ADMIN_IDS  # noqa: PLW0602
    _ADMIN_IDS = set()
    try:
        path = _admin_file()
        if path.exists():
            _ADMIN_IDS = {
                line.strip()
                for line in path.read_text(encoding='utf-8').splitlines()
                if line.strip()
            }
    except Exception:
        logger.error(traceback.format_exc())


def is_admin(platform_id) -> bool:
    """判断平台用户是否为管理员（优先全局缓存，缓存未命中时兜底读文件并同步）。"""
    pid = str(platform_id).strip()
    if pid in _ADMIN_IDS:
        return True
    try:
        path = _admin_file()
        if path.exists():
            ids = {
                line.strip()
                for line in path.read_text(encoding='utf-8').splitlines()
                if line.strip()
            }
            if pid in ids:
                _ADMIN_IDS.update(ids)
                return True
    except Exception:
        logger.error(traceback.format_exc())
    return False


def generate_check_admin() -> str:
    """生成 32 位随机校验串并写入 checkAdmin.txt（独立生成器）。

    仅当不存在 admin.txt 时生成；返回校验串，未生成时返回空串。
    """
    if _admin_file().exists():
        return ''
    token = secrets.token_hex(TOKEN_LENGTH // 2)  # 32 位十六进制随机串
    try:
        _check_file().write_text(token, encoding='utf-8')
    except Exception:
        logger.error(traceback.format_exc())
        return ''
    return token

def verify_and_add_admin(platform_id, token) -> int:
    """校验串验证 + 写入管理员ID 统一入口（一步执行完成）。

    发送者已在全局缓存（管理员）时直接成功，不再走校验流程。
    """
    pid = str(platform_id).strip()
    if pid in _ADMIN_IDS:
        return 2
    """校验用户发送的校验串是否与 checkAdmin.txt 一致（独立校验函数，无副作用）。"""
    try:
        stored = get_pending_check_token()
        if bool(stored) and str(token).strip() == stored:
            add_admin_id(platform_id)
            """验证成功后删除 checkAdmin.txt。"""
            _check_file().unlink(missing_ok=True)
            return 1
    except Exception:
        logger.error(traceback.format_exc())
    return 0


def get_pending_check_token():
    """读取当前待校验串（checkAdmin.txt），不存在时返回 None。"""
    path = _check_file()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding='utf-8').strip() or None
    except Exception:
        logger.error(traceback.format_exc())
        return None



def add_admin_id(platform_id) -> bool:
    """将平台用户ID写入 admin.txt（追加去重），并同步全局缓存。"""
    try:
        path = _admin_file()
        ids = []
        if path.exists():
            ids = [line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        target = str(platform_id).strip()
        if not target:
            return False
        if target not in ids:
            ids.append(target)
        path.write_text('\n'.join(ids) + '\n', encoding='utf-8')
        _ADMIN_IDS.add(target)
        return True
    except Exception:
        logger.error(traceback.format_exc())
        return False
