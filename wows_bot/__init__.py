"""wows-yuyuko 插件的实现包。

- `config_map.py`     插件配置 -> hikari-core 配置的映射
- `runtime.py`        hikari-core 的启动 / 就绪判断 / 收尾
- `selection.py`      「等用户回复序号」的选择流程
- `sender.py`         渲染结果的发送（图片走 base64 直发）
- `logging_bridge.py` 把 hikari-core 的 loguru 日志接进 MaiBot 日志系统

本文件负责把随仓库内置的 hikari-core 源码挂进 `sys.path`，因此**必须先于**
其它子模块被导入。用 append 而不是 insert(0)：插件根目录下还有 `config.py`
这类通用名文件，放最前面会和主程序/其他插件的顶级模块重名。
"""

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.append(str(_PLUGIN_ROOT))

if not (_PLUGIN_ROOT / "hikari_core" / "__init__.py").is_file():
    import warnings

    warnings.warn(
        f"内置的 hikari-core 源码缺失或不完整（{_PLUGIN_ROOT / 'hikari_core'}），"
        "插件将无法工作。请重新安装/更新本插件。",
        RuntimeWarning,
        stacklevel=2,
    )

from .logging_bridge import install as install_logging_bridge  # noqa: E402
from .runtime import CoreRuntime  # noqa: E402,F401
from .selection import SelectionManager  # noqa: E402,F401
from .sender import OutputSender, account_not_found_hint, reply_text  # noqa: E402,F401

install_logging_bridge()
