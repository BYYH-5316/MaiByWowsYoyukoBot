# 战舰世界战绩查询 · MaiBot 插件

在麦麦里发 `/wws`，把《战舰世界》的战绩「水表」直接渲染成图片发到群里。
支持 Windows（一键包 / 裸 Python）与 Docker 两种部署方式。

| 项 | 值 |
|---|---|
| 插件 ID | `community.wows-yuyuko` |
| 插件名称（市场标题） | 战舰世界战绩查询 |
| 插件类型 | `other`（其他） |
| 插件版本 | 1.0.0 |
| MaiBot 移植与维护 | 白云鱼海 |
| 运行环境 | MaiBot 1.x（`host_application` 1.0.0–1.99.99）+ 插件 SDK 2.6–2.99 |
| 内置渲染核心 | hikari-core `2.0.1`（随插件分发，不走 PyPI） |
| 数据源 | yuyuko API `https://v3-api.wows.shinoaki.com` |
| 仓库 | https://github.com/BYYH-5316/MaiByWowsYoyukoBot |

## 出处与致谢

本插件是 **AstrBot 插件在 MaiBot 上的移植版**，渲染核心与数据全部来自下列上游，版权归各原作者：

| 组件 | 作者 | 项目地址 |
|---|---|---|
| AstrByWowsYuyukoBot（原版 AstrBot 插件，本移植版的直接来源） | 西行寺雨季 | https://github.com/JustOneSummer/AstrByWowsYuyukoBot |
| hikari-core v2.0.1（内置渲染核心，MIT） | Hikari / benx1n | https://github.com/benx1n/HikariBot |
| yuyuko 战绩 API 与网页端 | yuyuko / shinoaki | https://v3-api.wows.shinoaki.com · https://wows.shinoaki.com |
| 渲染模板与静态资源 | hikari-resource | 插件启动时自动拉取 |

完整声明（含数据与商标说明）见 [NOTICE.md](NOTICE.md)，许可证见 [LICENSE](LICENSE)。
本仓库只新增 MaiBot 适配层（`plugin.py`、`config.py`、`wows_bot/`）与部署文档，**未修改 `hikari_core/`**。

> 原版仓库目前未声明开源许可证，本移植版按 MIT 发布并保留原作者完整署名；
> 如上游作者对分发方式有异议，请开 Issue 告知。

---

## 1. 功能

- `/wws <参数>`：帮助、绑定 / 解绑 / 切换绑定、单船战绩、近期战绩、公会、排行等全部子指令，结果统一渲染成图片
- 交互式「回复序号选择」：一条查询返回多个候选时，直接回数字即可继续（20 秒超时，按会话隔离）
- 数据实时：战绩、公会、排行每次指令实时请求 yuyuko API；模板每天 04:00 / 12:00 更新，战舰资源每周四 08:00 更新
- 渲染结果以 base64 直接发图，不落盘；卸载时自动停调度器、关浏览器、清临时产物

## 2. 环境要求

| 项 | 要求 |
|---|---|
| 麦麦 | MaiBot 1.x，插件 SDK ≥ 2.6.0 |
| Python | 使用麦麦自带的运行环境即可（≥ 3.10） |
| 磁盘 | 约 400 MB（浏览器内核 ~360 MB + 战舰资源 ~22 MB） |
| 网络 | 首次启动需能访问 yuyuko API、模板 OSS 与 npmmirror（下载 Chromium 内核）；国内网络一般无需代理 |

## 3. 安装依赖

插件自带的 `requirements.txt` 已列全依赖。**正常路径下 MaiBot 会自动安装**（加载前读 `_manifest.json`
的 `dependencies`），下面三种情况才需要手动装：

- 一键包版本较旧，不会自动按 manifest 装依赖
- 机器离线安装 / 需要走镜像加速
- 排障时要补装或重装某个包

在麦麦的 Python 运行环境中，进入本插件目录后执行：

```bash
uv pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple
```

未使用 uv 时，将命令中的 `uv pip` 改为 `pip` 即可：

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple
```

> 一键包用户可在**启动菜单的「交互式安装 pip 模块」**中安装本插件的 `requirements.txt`。

**Docker 部署**（容器内没有激活 venv，需显式指定解释器）：

```bash
docker exec -it maim-bot-core sh -lc \
  'cd /MaiMBot/plugins/MaiByWowsYoyukoBot && uv pip install -r requirements.txt \
   -i https://mirrors.aliyun.com/pypi/simple --python /MaiMBot/.venv/bin/python'
```

依赖清单与注意点：

| 包 | 用途 | 注意 |
|---|---|---|
| `httpx[http2]` | 访问 yuyuko API | 附带安装 `h2`；缺了会报 `Using http2=True, but the 'h2' package is not installed` |
| `APScheduler` | 模板 / 战舰资源定时更新 | 版本过低时 hikari-core 会报错 |
| `pydantic` | hikari-core 配置与数据模型 | 必须 v2 |
| `loguru` | hikari-core 日志 | 已桥接进麦麦日志面板 |
| `pillow` | 图片合成（GIF、头像裁切） | — |
| `playwright==1.58.0` | 截图内核 | **钉死版本**：浏览器构建号与之绑定（1.58.0 → `chromium-1208`），升级会让插件重新下载内核，详见第 7 节 |

## 4. 部署

### 4.1 放置插件源码

从仓库拉取（推荐，更新时 `git pull` 即可）：

```bash
cd <麦麦根目录>/plugins
git clone https://github.com/BYYH-5316/MaiByWowsYoyukoBot
```

没有 git 就下载 ZIP 解压到 `plugins/` 下。**主文件夹名建议与仓库同名**（`MaiByWowsYoyukoBot`，全英文，
避免中文/空格路径在部分环境下的编码问题）；插件数据目录按 `_manifest.json` 的 `id`
（`community.wows-yuyuko`）命名，与源码目录名无关，改名不会丢数据。

| 部署方式 | 插件源码位置 | 插件数据位置（自动创建） |
|---|---|---|
| Windows 一键包 | `MaiBot\plugins\MaiByWowsYoyukoBot\` | `MaiBot\data\plugins\community.wows-yuyuko\` |
| Windows 裸 Python | `<安装目录>\plugins\MaiByWowsYoyukoBot\` | `<安装目录>\data\plugins\community.wows-yuyuko\` |
| Docker | 宿主 `/root/data/MaiMBot/plugins/MaiByWowsYoyukoBot` → 容器 `/MaiMBot/plugins/MaiByWowsYoyukoBot` | 宿主 `/root/data/MaiMBot-plugin-data/community.wows-yuyuko` → 容器 `/MaiMBot/data/plugins/community.wows-yuyuko` |

> 数据目录存的是浏览器内核、战舰资源、头像缓存与 `admin.txt`。重装 / 更新插件时**不要删它**，
> 否则下次启动要重新下载约 400 MB。

### 4.2 启用插件并填 Token

1. 打开麦麦 WebUI → 插件页，找到「**战舰世界战绩查询**」，点击启用；
2. 在插件配置里填入 `wows_token`（获取方式见第 8.3 节）；
3. 保存。`[plugin] enabled = true` 由面板写入，无需手改 `config.toml`。

### 4.3 重启载入

- **Windows 一键包**：关闭程序窗口，用启动菜单重新启动麦麦（改代码 / 改 manifest 后必须重启，改配置不用）
- **Windows 裸 Python**：`Ctrl+C` 停掉 `python bot.py`，重新启动
- **Docker**：

```bash
docker restart maim-bot-core
```

### 4.4 验收

日志前缀是 `[plugin.community.wows-yuyuko]`，按顺序看到这几条就说明正常：

```bash
# Docker
docker logs -f --since 2m maim-bot-core 2>&1 | grep -E 'wows|hikari|模板|战舰|Playwright'
# Windows：看麦麦控制台或 logs/ 下的日志文件
```

| 日志 | 含义 |
|---|---|
| `模板更新完成：写入 N 个，未变化 25 个，失败 0 个` | 模板拉取正常 |
| `开始更新战舰资源` → `更新战舰资源完成` | 舰船数据/图标正常 |
| `[wows] Playwright 浏览器内核已就绪: ...` | 截图内核可用 |
| `[扩展插件] 已启动：... 已加载=N，失败=0` | 插件加载成功 |

群里冒烟测试（分别覆盖「纯文本返回」和「真实 API + 渲染」两条路径）：

```
/wws help
/wws clan cn ABCD
```

首次启动要下载模板与战舰资源，期间指令会回「正在准备渲染资源，请稍等一会儿再查询哦~」。
启动等待上限 300 秒，超时后**后台继续下载**，就绪前一律提示稍后再试，不会卡住事件循环。

## 5. 更新

```bash
cd <麦麦根目录>/plugins/MaiByWowsYoyukoBot
git pull            # 或重新下载 ZIP 覆盖（别覆盖 config.toml / config_back/）
```

然后按 4.3 重启。升级提示中的版本号来自 `_manifest.json` 的 `version`，本版本为 `1.0.0`。

若使用 WebUI 的插件市场安装 / 升级，本仓库的 Git Tag 必须与 `_manifest.json` 的 `version` 完全一致
（例如 tag `1.0.0` 对应 `"version": "1.0.0"`），否则市场会报版本不匹配。

## 6. 配置项

WebUI 里按表单填写即可，落盘为 `config.toml` 的 `[wows]` 节：

| 字段 | 默认 | 说明 |
|---|---|---|
| `wows_token` | 公开测试 token | yuyuko API token，正式使用请换成自己的 |
| `use_browser` | `chromium` | 截图内核，`chromium` / `firefox`；chromium 性能约为 firefox 三倍 |
| `image_type` | `jpeg` | `jpeg` / `png` / `webp`；png/webp 体积更小但稍慢 |
| `user_image_cache_ttl_minutes` | `10080` | 头像/横幅本地缓存分钟数，`<=0` 永不过期 |
| `proxy_enabled` / `proxy_url` | `false` / `http://127.0.0.1:7890` | 访问 WG / yuyuko 的代理，仅启用时生效 |
| `http2_enabled` | `true` | 关掉就不需要 `h2`，但失去 HTTP/2 并发 |
| `local_test` | `false` | 调试模式：额外落盘渲染用 HTML，同时跳过定时任务 |
| `save_template_html` | `false` | 出图时把 HTML 存到数据目录 `template_html/` |
| `render_error_fallback` | `false` | 渲染失败时退回纯文本错误（关闭则把报错画进图片） |
| `[plugin] config_version` | `1.0.0` | 配置 schema 版本，**别手改**（面板里是隐藏禁用项） |

改完保存即热更新：插件会重新下发 hikari-core 配置，不重建浏览器、不用重启。

### 管理员指令

启动日志里会出现一次 32 位校验串，**把整条消息原样发给机器人（建议私信）**即可成为管理员：
成功后写数据目录下的 `admin.txt`（持久），并删除 `checkAdmin.txt`。管理员独有：

```
/wws 更新样式     /wws 更新战舰     /wws 检查更新
```

## 7. 渲染依赖：浏览器内核与字体

### 7.1 自动安装（Windows / 首次启动都适用）

插件启动时会检查数据目录下的 `browsers/`，缺失/过期就自动执行 `playwright install chromium`，
下载源已内置为 `https://npmmirror.com/mirrors/playwright/`，下载完成会写缓存标记
`browsers/.chromium-env`（两行：`PLAYWRIGHT_BROWSERS_PATH=...`、`REVISION=1208`）。

- **Windows**：无需额外步骤，也不需要装 Linux 系统依赖
- **Docker / Linux**：安装脚本会在 `pip` 之外再跑一次 `playwright install-deps`（需要 root 与网络），
  部分精简镜像会失败，此时手工补依赖或按下文预置内核

### 7.2 手工预置内核（网络差 / 离线）

把 Chrome for Testing 按 Playwright 的目录规范放到 `<数据目录>/browsers/`：

```
<数据目录>/browsers/
├── chromium-1208/chrome-linux64/chrome          # Linux；Windows 为 chromium-1208/chrome-win/chrome.exe
├── chromium-1208/chrome-linux64/chrome_sandbox  # 仅 Linux，必须 4755
└── .chromium-env                                 # 缓存标记，REVISION 必须与已装 playwright 期望值一致
```

```bash
# 仅 Linux/Docker 需要：权限不对会在 chrome_crashpad_handler 报 Permission denied
docker exec maim-bot-core chmod -R 755 /MaiMBot/data/plugins/community.wows-yuyuko/browsers
docker exec maim-bot-core chmod 4755 /MaiMBot/data/plugins/community.wows-yuyuko/browsers/chromium-1208/chrome-linux64/chrome_sandbox
```

只有 `find_executable` 真的找到可执行文件时才写标记，所以一次失败不会「永不重试」。

### 7.3 中文字体

- **Windows**：系统自带「微软雅黑」，与上游模板 CSS 首选字体一致，出图效果最好
- **Docker / Linux**：模板 CSS 会回退到文泉驿 / 思源（容器内一般已有），能出图但字形略有差异。
  要和上游截图完全一致，把 `msyh.ttc` 拷进容器并刷新字体缓存：

```bash
docker exec maim-bot-core mkdir -p /usr/share/fonts/truetype/microsoft
docker cp msyh.ttc maim-bot-core:/usr/share/fonts/truetype/microsoft/
docker exec maim-bot-core fc-cache -fv
docker restart maim-bot-core
```

> `/usr/share/fonts` 不在挂载表里，容器重建后字体会丢；要长期保留请做成卷挂载或写进自定义镜像。

## 8. 日常维护

### 8.1 不用管的部分

| 项目 | 时机 | 来源 |
|---|---|---|
| 渲染模板 | 每天 04:00 / 12:00，另加每次启动 | `hikari-resource` OSS 的 `template-v2.json` |
| 战舰资源（新船、图标、中英文名） | 每周四 08:00 | yuyuko `nahida-static` |
| 战绩 / 公会 / 排行 | 每次指令实时请求 | yuyuko API |

WG 加新船、改数值都不需要人工干预；想立刻看到就 `/wws 更新战舰`（配合 `/wws 更新样式`）。

### 8.2 升级 hikari-core

1. 备份：`cp -r hikari_core hikari_core.bak`
2. 从上游取新的 `hikari_core/` **整目录覆盖**（本项目的补丁都在 `wows_bot/` 适配层，不含 `hikari_core/` 内改动）
3. 重启麦麦；看日志「失败=0」，确认模板 / 战舰资源更新无报错
4. 冒烟：`/wws help`、`/wws clan cn ABCD`
5. 异常就回滚备份目录再重启

> ⚠️ **别信 `/wws 检查更新`**：它拉的上游 `version.json` 只登记到 `1.1.3`（2025-03-10），v2 线没登记，
> 而且是字符串比较，对内置的 `2.0.1` 永远回答「已经是最新版本了」。是否升级请看上游仓库
> `hikari_core/__init__.py` 里的 `__version__`。

### 8.3 获取 yuyuko API Token

1. 加 QQ 群 **967546463**，按群公告提交申请（yuyuko / Hikari 生态统一渠道）；
2. 通过后 token **通过邮件下发**，格式固定为 `<申请时使用的QQ号>:<邮件里的token>`；
3. 网页端 `https://wows.shinoaki.com` 只提供战绩查询（查自己的 AID、复制特殊绑定指令），**没有 token 申请入口**；
4. `config.py` 里内置的是**公开测试 token**，只够跑通流程，随时可能被限流，正式使用请换成自己申请的；
5. 失效表现：所有指令都回上游短错误，日志里 `POST https://v3-api.wows.shinoaki.com/...` 之后紧跟 `failed`。

### 8.4 备份清单

- 插件源码目录（**含填过 token 的 `config.toml`，备份等于备份凭据**）
- 数据目录里的 `browsers/`（重装最费时）、`ship_cache*`、`admin.txt`
- 截图产物 `file_img_temp/`、`browser_temp/` 可随手删

## 9. 指令速查

发 `/wws help` 看完整列表。绑定族：

```
/wws bind cn 游戏昵称            昵称绑定（也可写「绑定 国服 昵称」）
/wws bind cn (First Last)        昵称含空格：整体用半角括号包住
/wws 特殊绑定 <AID>              用 AID 直接绑定，完全跳过昵称搜索
/wws 查绑定                      绑定列表
/wws 切换绑定 [序号]              多账号切换，/wws 删除绑定 同理
@机器人 /wws xxx                 群聊里 @ 也可以
纯数字回复                       消费上一条查询的「选择序号」流程，超时自动退出
```

服务器关键词：`cn` / 国服、`asia` / 亚服、`eu` / 欧服、`na` / 美服、`ru` / 俄服。

### 「用户不存在」不是故障

yuyuko 的昵称搜索是**精确匹配**，实测结果：

| 输入 | 上游返回 |
|---|---|
| 完整真实昵称 + 正确服务器 | `code=200`，命中 1 条 |
| 只给前缀（想模糊搜） | `code=501`，上游超时——不支持模糊 |
| 同一昵称查错服务器 | `code=404 用户名不符合规范!` |
| 昵称尾部多一个空格 | `code=540` |
| 昵称写错 / 改过名 | `code=404 用户不存在` |

所以这句只会是**填错服务器或昵称**。插件已把上游原文替换为「实际使用的服务器 + 昵称 + 四步自查」的提示；
要立刻绕过就发 `/wws 特殊绑定 <AID>`。

## 10. 排障

日志前缀 `[plugin.community.wows-yuyuko]`。每条 `/wws` 都会记录
`指令 '<原始参数>' user=… group=…` 与 `结果 status=… server=… 昵称=… aid=…`。

| 现象 | 原因 | 处置 |
|---|---|---|
| 回复里带 `the 'h2' package is not installed` | 开了 http2 但缺 `h2` | 按第 3 节装 `requirements.txt`（或 `uv pip install h2`） |
| 一直回「正在准备渲染资源」 | 首次下载没完成 / 启动超 300 秒 | 看日志确认后台在推进；网络差就把内核与战舰缓存预置好（7.2） |
| 启动即 `FileNotFoundError: ship_cache_hash.json` | 上次下载被打断留下半截 `ship_cache.zip` | 已自愈：插件每次启动校验 zip、补解压、重拉清单；仍失败就删数据目录里的 `ship_cache.zip` / `ship_cache/` / `ship_cache_hash.json` 再重启 |
| 找不到 chrome / `chrome_crashpad_handler` Permission denied | 内核缺失或权限不对（仅 Linux） | 见 7.2：`chmod 755` 目录 + `4755` `chrome_sandbox`，并核对 `.chromium-env` 的 `REVISION` |
| playwright 升级后开始重装内核 | `browsers.json` 期望构建号变了 | 预置新 `chromium-<rev>/` 并改 `.chromium-env`，或把 playwright 降回 `1.58.0` |
| 「图片发送失败了」 | 通道权限 / 图片体积限制 | 确认机器人有发图权限；`image_type=jpeg` 体积最小 |
| 多人同时查询明显变慢 | 渲染是单浏览器串行（插件内置渲染锁），故意如此 | 正常排队；并发会重复拉起浏览器进程，不要去掉 |
| 警告「模板清单里没有 nunjucks.min.js / hikari-render.js」 | 上游清单不带这两个 JS | 正常，渲染器随插件分发在 `hikari_core/Template/` 下 |
| 警告「未加载 data_user 私有模块…wws auth 将提示未部署」 | 上游可选私有模块没部署 | 正常，本插件不使用 `wws auth` |
| WebUI 插件详情读不出 README | 插件根目录缺 `README.md`（读不到才去 GitHub Raw 拉上游 README） | 确保 `plugins/MaiByWowsYoyukoBot/README.md` 存在（就是本文件） |
| 改完代码没生效 | 需要重启 | Windows 重启麦麦；Docker `docker restart maim-bot-core` |
| 插件列表里版本号还是旧的 | `_manifest.json` 没更新或未重启 | 改 `version` 后重启，必要时在插件页点「刷新」 |

## 11. 卸载

1. WebUI 里停用插件（触发清理：停 APScheduler、关浏览器进程、清 `file_img_temp/`）；
2. 删源码目录 `plugins/MaiByWowsYoyukoBot`；
3. 需要回收磁盘再删数据目录 `data/plugins/community.wows-yuyuko`
   （想重装就留着，`browsers/` 与 `ship_cache/` 都能直接复用）。

## 12. 许可证

本移植版以 [MIT](LICENSE) 发布。上游组件版权与授权详见 [NOTICE.md](NOTICE.md)：

- 原版插件 AstrByWowsYuyukoBot © 西行寺雨季 — https://github.com/JustOneSummer/AstrByWowsYuyukoBot
- 渲染核心 hikari-core v2.0.1 © Hikari / benx1n（MIT）— https://github.com/benx1n/HikariBot
- 战绩数据与图片样式归 yuyuko / Wargaming.net 所有，本插件仅做接入展示
