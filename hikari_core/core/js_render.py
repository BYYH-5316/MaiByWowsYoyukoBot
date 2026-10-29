"""浏览器端渲染（Nunjucks）—— 生成可交给截图服务的 HTML 外壳。

▍定位：这是**唯一**的渲染路径（2026-09-19 起服务端的 Jinja 引擎已删除）
「Python 只打包数据与模板源码 → 浏览器里用 Nunjucks 渲染」，
好处是**成品截图与本地预览/编辑器共用同一份模板与同一条渲染路径**，
「预览即成品」不再依赖两份实现手工对齐。
原来的服务端 Jinja 渲染已彻底移除（依赖、环境、代码路径全部删掉了）：迁移后的模板用了
Nunjucks 专有写法（dget() 全局 / .push / .slice / 循环内 {% set %} 累积），Jinja 渲染不了它们。
现在的回归门禁也不再用 Jinja —— 它只做「真在浏览器里渲染一遍」的冒烟检查 + 模板编译检查，
见 tests/js_render_guard.py。

▍为什么模板源码要内联，而不是让浏览器去 fetch
渲染产物是以「写临时文件 → 打开 file://」的方式加载的，而 file:// 下
fetch 本地文件会被 CORS 拦掉（实测 Failed to fetch）。所以本模块把入口模板
及其依赖（顺着 from / import / include / extends 递归收）**内联**进外壳 HTML，
浏览器端 hikari-render.js 用内存 loader 交给 Nunjucks。

▍职责边界
本模块只负责「打包 + 组装外壳」。数据预处理（enrich_banner_dark /
enrich_poster_opacity / find_and_modify_shipinfo）仍由调用方先做完再传进来 ——
那些是数据加工，不是渲染。
"""

import json
from pathlib import Path
from typing import Optional

from .constants import template_path
from .render_helpers import _BA_EM, _SERVER_CN  # noqa: PLC2701  复用同一份真值表，避免两份漂移

# 外壳要引的两个本地资源（与 main-v6.js / echarts.js 同级放在 Template/ 下）
RENDER_ASSETS = ('nunjucks.min.js', 'hikari-render.js')

# 模板之间的引用方式。extends 也一起收，虽然 v6 目前没用，但漏掉会很难查。
_IMPORT_PATTERN = r"""{%-?\s*(?:from|import|include|extends)\s+['"]([^'"]+)['"]"""


class JsRenderError(Exception):
    """浏览器端渲染相关的组装错误（缺资源 / 缺模板）。"""


def _root(root: Optional[Path] = None) -> Path:
    return Path(root) if root else Path(template_path)


def assert_assets(root: Optional[Path] = None) -> None:
    """检查本地渲染资源是否齐备。缺了就直接报错，别让它悄悄退化成"白页"。"""
    base = _root(root)
    missing = [name for name in RENDER_ASSETS if not (base / name).exists()]
    if missing:
        raise JsRenderError(
            f'浏览器端渲染需要 {base} 下存在 {" / ".join(RENDER_ASSETS)}，缺少: {", ".join(missing)}'
        )


def collect_templates(entry: str, root: Optional[Path] = None) -> dict:
    """从入口模板出发，递归收集它引用到的全部模板源码。

    Args:
        entry: 入口模板文件名（相对 template_path，如 ``wws-clan-v6.html``）
        root: 模板根目录，缺省用 ``constants.template_path``

    Returns:
        {模板名: 源码}，键与模板里的引用写法一致（如 ``partials/clan-v6-macros.html``）
    """
    import re

    base = _root(root)
    entry_path = base / entry
    if not entry_path.exists():
        raise JsRenderError(f'找不到入口模板: {entry_path}')

    gathered: dict = {}
    pending = [entry]
    while pending:
        name = pending.pop()
        if name in gathered:
            continue
        path = base / name
        if not path.exists():
            raise JsRenderError(f'模板 {name} 被引用但不存在（来自 {entry} 的引用链）')
        source = path.read_text(encoding='utf-8')
        gathered[name] = source
        pending.extend(re.findall(_IMPORT_PATTERN, source, re.S))
    return gathered


def compat_tables() -> dict:
    """浏览器端兼容层需要的真值表。

    ▍**不要**在 JS 侧另抄一份：``ba_text_em`` 的字表是字体实测数据，
      近似实现会让 logo 宽度跑偏（文本全对、像素对不上）；``server_cn`` 的别名表
      同理。这里直接把 Python 侧的真值序列化过去，保证两边永远一致。
    """
    return {
        'baEm': dict(_BA_EM),
        'serverCn': dict(_SERVER_CN),
    }


def build_payload(
    entry: str,
    data,
    *,
    root: Optional[Path] = None,
    asset_base: Optional[str] = None,
    frozen_time: Optional[float] = None,
) -> dict:
    """打包成浏览器端需要的载荷。

    Args:
        entry: 入口模板文件名
        data: 渲染数据（已完成 enrich 等预处理）
        root: 模板根目录
        asset_base: 模板里 ``template_path.as_uri()`` 拼出的前缀，缺省为模板目录的 file:// 地址
        frozen_time: 冻结 ``time.time()``（仅回归比对用；不传则用浏览器当前时间）

    ▍``assets`` 是模板目录的**顶层文件名清单**（只给文件名，不含路径）。浏览器侧拿它实现
    ``asset_exists('echarts.js')`` —— 只有一个进程能看文件系统，就是这里；模板里要判断
    「某个本地资源在不在」时必须走它，别用 pathlib 运算（JS 侧没有，条件会静默恒假）。
    """
    base = _root(root)
    payload = {
        'entry': entry,
        'templates': collect_templates(entry, base),
        'data': data,
        'assetBase': asset_base or base.as_uri(),
        # 顶层文件（echarts.js / main-v6.js / 各 css 都在这一层）。目录本身不在清单里。
        'assets': sorted(item.name for item in base.iterdir() if item.is_file()),
        'frozenTime': frozen_time,
    }
    payload.update(compat_tables())
    return payload


def _escape_for_script(value) -> str:
    """把 JSON 放进 <script> 里必须转义 '<'。

    模板源码本身含 ``</script>``（页面要引 echarts 之类），不转义会提前闭合脚本块、
    整页直接崩。``\\u003c`` 是合法 JSON 转义，解析结果不变。
    """
    return json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')


def render_shell(
    entry: str,
    data,
    *,
    root: Optional[Path] = None,
    template_path_uri: Optional[str] = None,
    frozen_time: Optional[float] = None,
) -> str:
    """组装「浏览器端渲染」用的外壳 HTML（可直接交给 ``html_to_pic``）。

    外壳里只有三样东西：本地 nunjucks、内联载荷、本地渲染器 + 一次 boot 调用。
    浏览器打开后 hikari-render.js 会用内联的模板与数据渲染出真正的整页，
    并置 ``window.__hikari_render_done``（截图服务等这个标记）。

    Args:
        entry: 入口模板文件名
        data: 渲染数据（已完成预处理）
        root: 模板根目录，缺省 ``constants.template_path``
        template_path_uri: 资源前缀，缺省模板目录的 file:// 地址
        frozen_time: 冻结 ``time.time()``（仅回归比对用）
    """
    base = _root(root)
    assert_assets(base)
    payload = build_payload(entry, data, root=base, asset_base=template_path_uri, frozen_time=frozen_time)
    prefix = (template_path_uri or base.as_uri()).rstrip('/')
    return (
        '<!DOCTYPE html>\n'
        '<html lang="zh-cn">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        f'<script src="{prefix}/nunjucks.min.js"></script>\n'
        f'<script id="hikari-payload" type="application/json">{_escape_for_script(payload)}</script>\n'
        f'<script src="{prefix}/hikari-render.js"></script>\n'
        '<script>window.HikariRender.boot();</script>\n'
        '</head>\n'
        '<body></body>\n'
        '</html>\n'
    )
