# 战舰世界战绩查询 · MaiBot 插件

> **文档版本 1.2.0** · 对应插件 **1.2.0** · 配置 schema **1.4.0**（2026-10-31）

在麦麦里发 `/wws`，把《战舰世界》的战绩「水表」直接渲染成图片发到群里。
支持 Windows（一键包 / 裸 Python）与 Docker 两种部署方式。

| 项 | 值 |
|---|---|
| 插件 ID | `community.wows-yuyuko` |
| 插件名称（市场标题） | 战舰世界战绩查询 |
| 插件类型 | `game`（游戏） |
| 插件版本 | **1.2.0** |
| 配置 schema | `1.4.0`（`[plugin] config_version`，面板里隐藏） |
| 插件图标 | `assets/icon.png`（经 manifest 的 `display.icon` 声明，显示在 WebUI 插件页） |
| MaiBot 移植与维护 | 白云鱼海 |
| 运行环境 | MaiBot 1.0.0–1.3.99（实测 1.3.15）+ 插件 SDK 2.6.0–2.16.99（实测 2.16.0） |
| 内置渲染核心 | hikari-core `2.0.1`（随插件分发，不走 PyPI，含 3 处本地安全补丁） |
| 数据源 | yuyuko API `https://v3-api.wows.shinoaki.com` |
| 仓库 | https://github.com/BYYH-5316/MaiByWowsYoyukoBot |

## 目录

1. [功能一览](#1-功能一览)
2. [安装与部署](#2-安装与部署)
3. [配置详解（按 WebUI 分组顺序）](#3-配置详解按-webui-分组顺序)
4. [使用方法](#4-使用方法)
5. [渲染依赖：浏览器内核与字体](#5-渲染依赖浏览器内核与字体)
6. [日常维护](#6-日常维护)
7. [排障](#7-排障)
8. [卸载](#8-卸载)
9. [出处与许可](#9-出处与许可)

---

## 1. 功能一览

- **`/wws` 指令**：帮助、绑定 / 解绑 / 切换绑定、单船战绩、近期战绩、公会、排行等全部子指令，结果统一渲染成图片
- **唤起方式可配置**（`[trigger]` 组）：指令词、是否必须带 `/` 前缀、群里是否必须 @ 机器人，都可以在 WebUI 里改
- **AI 对话查询**（实验性，默认关闭）：不用记指令，直接说「查一下我的战绩」「国服 Alice_01 最近一周打得怎么样」，AI 调工具取数、发图并点评
- **群里 @ 群友查水表**（实验性，默认关闭）：@ 某位群友、同时叫到机器人并说「水表 / 战绩」，插件直接查 TA 绑定账号并发图
- **交互式选择**：一条查询返回多个候选时，直接回数字继续（20 秒超时，按会话隔离）
- **数据实时**：战绩 / 公会 / 排行每次实时请求；模板每天 04:00、12:00 更新，战舰资源每周四 08:00 更新
- **零落盘出图**：渲染结果以 base64 直接发送；卸载时自动停调度器、关浏览器、清临时产物

> 两个实验性功能（AI 对话查询、群聊 @ 查询）都有独立开关，**默认关闭**。
> AI 可能误触发或乱编数据，@ 查询会抢占群消息，想用请在 WebUI 对应分组里手动打开。

## 2. 安装与部署

### 2.1 环境要求

| 项 | 要求 |
|---|---|
| 麦麦 | MaiBot 1.x，插件 SDK ≥ 2.6.0 |
| Python | 使用麦麦自带的运行环境即可（≥ 3.10） |
| 磁盘 | 约 400 MB（浏览器内核 ~360 MB + 战舰资源 ~22 MB） |
| 网络 | 首次启动需能访问 yuyuko API、模板 OSS 与 npmmirror（下载 Chromium 内核）；国内网络一般无需代理 |

### 2.2 放置插件源码

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

> **从插件市场安装**时，目录会按 `plugin_id` 命名：`community_wows-yuyuko`（点换成下划线），与仓库名不同。
> 两种装法的数据目录都是 `community.wows-yuyuko`，互不影响。
>
> 数据目录存的是浏览器内核、战舰资源、头像缓存与 `admin.txt`。重装 / 更新插件时**不要删它**，
> 否则下次启动要重新下载约 400 MB。

### 2.3 安装依赖

插件自带的 `requirements.txt` 已列全依赖。**正常路径下 MaiBot 会自动安装**（加载前读 `_manifest.json`
的 `dependencies`），下面三种情况才需要手动装：

- 一键包版本较旧，不会自动按 manifest 装依赖
- 机器离线安装 / 需要走镜像加速
- 排障时要补装或重装某个包

在麦麦的 Python 运行环境中，进入本插件目录后执行：

```bash
uv pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple
```

未使用 uv 时，把 `uv pip` 改成 `pip`。一键包用户可在**启动菜单的「交互式安装 pip 模块」**中安装。

**Docker 部署**（容器内没有激活 venv，需显式指定解释器；下面的目录按手动 clone 的写法，
市场安装时换成 `community_wows-yuyuko`）：

```bash
docker exec -it maim-bot-core sh -lc \
  'cd /MaiMBot/plugins/MaiByWowsYoyukoBot && uv pip install -r requirements.txt \
   -i https://mirrors.aliyun.com/pypi/simple --python /MaiMBot/.venv/bin/python'
```

| 包 | 用途 | 注意 |
|---|---|---|
| `httpx[http2]` | 访问 yuyuko API | 附带安装 `h2`；缺了会报 `Using http2=True, but the 'h2' package is not installed` |
| `APScheduler` | 模板 / 战舰资源定时更新 | 版本过低时 hikari-core 会报错 |
| `pydantic` | hikari-core 配置与数据模型 | 必须 v2 |
| `loguru` | hikari-core 日志 | 已桥接进麦麦日志面板 |
| `pillow` | 图片合成（GIF、头像裁切） | — |
| `playwright==1.58.0` | 截图内核 | **钉死版本**：浏览器构建号与之绑定（1.58.0 → `chromium-1208`），升级会让插件重新下载内核 |

### 2.4 获取并填写 Token

**必填项**：插件不自带任何 token。未填写时，除 `/wws help` 外的指令都会被拦截，只回复一句
「还没有配置 yuyuko API Token」并告诉你填在 WebUI 哪里、格式是什么，不会向 yuyuko 发请求。

1. 加 QQ 群 **967546463**，按群公告提交申请（yuyuko / Hikari 生态统一渠道）；
2. 通过后 token **通过邮件下发**，格式固定为 `<申请时使用的QQ号>:<邮件里的token>`，**原样粘贴**，
   冒号不能省、前后不要留空格；
3. 网页端 `https://wows.shinoaki.com` 只提供战绩查询（查自己的 AID、复制特殊绑定指令），
   **没有 token 申请入口**；
4. 填写位置：WebUI → 插件页 → 战舰世界战绩查询 → 配置 → **[战绩查询与渲染] → API Token**，
   保存即热更新，不用重启；
5. 填错 / 失效的表现：`/wws 近期` 回上游原文 `请求头不符合标准;uri=/api/wows/recent/day/info`，
   日志里出现 `{'code': 409, ...}`；被限流或封禁时回鉴权 / 频率类短错误。
   token 没有自助续期，重新申请即可。

### 2.5 重启与验收

- **Windows 一键包**：关闭程序窗口，用启动菜单重新启动麦麦
- **Windows 裸 Python**：`Ctrl+C` 停掉 `python bot.py`，重新启动
- **Docker**：

```bash
docker restart maim-bot-core
```

> 容器里插件源码目录被 watchfiles 监视，**只改 `.py` 会自动重载**（日志出现 `配置已重新下发`）；
> 改 manifest、依赖或 Windows 一键包仍建议完整重启。

日志前缀是 `[plugin.community.wows-yuyuko]`，按顺序看到这几条就说明正常：

```bash
# Docker
docker logs -f --since 2m maim-bot-core 2>&1 | grep -E 'wows|hikari|模板|战舰|Playwright'
# Windows：看麦麦控制台或 logs/ 下的日志文件
```

| 日志 | 含义 |
|---|---|
| `模板更新完成：写入 N 个，未变化 25 个，失败 0 个` | 模板拉取正常 |
| `开始更新战舰资源` → `更新战舰资源完成` | 舰船数据 / 图标正常 |
| `[wows] Playwright 浏览器内核已就绪: ...` | 截图内核可用 |
| `[扩展插件] 已启动：... 已加载=N，失败=0` | 插件加载成功 |

群里冒烟测试（分别覆盖「纯文本返回」「真实 API + 渲染」「AI 工具」三条路径）：

```
/wws help
/wws clan cn ABCD
查一下我的战绩          # 需已绑定账号，且 [llm_tool] enabled = true
```

首次启动要下载模板与战舰资源，期间指令会回「正在准备渲染资源，请稍等一会儿再查询哦~」。
启动等待上限 300 秒，超时后**后台继续下载**，就绪前一律提示稍后再试，不会卡住事件循环。

### 2.6 更新

```bash
cd <麦麦根目录>/plugins/MaiByWowsYoyukoBot
git pull            # 或重新下载 ZIP 覆盖（别覆盖 config.toml / config_back/）
```

然后按 2.5 重启。升级提示中的版本号来自 `_manifest.json` 的 `version`，本版本为 `1.2.0`。

若使用 WebUI 的插件市场安装 / 升级，本仓库的 Git Tag 必须与 `_manifest.json` 的 `version` 完全一致
（例如 tag `1.2.0` 对应 `"version": "1.2.0"`，或 `v1.2.0`），否则市场会报版本不匹配。

---

## 3. 配置详解（按 WebUI 分组顺序）

本节顺序与 WebUI 配置页**从上到下**一致：

1. 插件设置（`[plugin]`）
2. 唤起方式（`[trigger]`）
3. 战绩查询与渲染（`[wows]`）
4. AI 对话查询（`[llm_tool]`，实验性）
5. 群聊 @ 查询（`[mention_query]`，实验性）

改完保存即热更新：插件会重新下发 hikari-core 配置，不重建浏览器、不用重启。

**开箱默认值**：装好之后，除了必填的 `wows_token`，其余各项都是本节表格里的默认值，也就是——

| 分组 | 默认状态 |
|---|---|
| 插件设置 | 启用（`enabled = true`），**唯一需要你动手的是 `wows_token`** |
| 唤起方式 | 指令词 `wws`，**不强制** `/` 前缀、**不强制** @ 机器人（与旧版行为一致） |
| 战绩查询与渲染 | chromium 截图、jpeg 输出、头像缓存 7 天、不挂代理、开 http2、调试与兜底全关 |
| AI 对话查询 | **关闭**（实验性），打开后 `require_at` 默认要求群聊 @ 机器人 |
| 群聊 @ 查询 | **关闭**（实验性），打开后要求关键词 + 叫到机器人 |

换句话说：**填好 token、启用插件，`/wws` 就能用**；两组实验性功能按需要手动打开。
下文每张表里的「默认」列就是这个开箱值，改过的项会和它不一致，排障时可以先对照一下。

### 3.1 插件设置（`[plugin]`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 插件总开关，由 WebUI 写入 |
| `config_version` | `1.4.0` | 配置 schema 版本，**别手改**（面板里是隐藏禁用项） |

### 3.2 唤起方式（`[trigger]`）

控制**群里怎么叫出插件**，默认保持老习惯（`/wws`、`@机器人 wws`、`wws` 都能用）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `command_aliases` | `wws` | 可用的指令词，逗号分隔；内置可选 `wws`、`水表`、`战绩`、`战舰`、`战舰世界`。只有这里列出的词才会触发指令 |
| `require_prefix` | `false` | 打开后群聊里必须带 `/`（或 `!`）前缀、或 @ 机器人，裸写「wws …」不再触发；私聊不受限 |
| `require_at` | `false` | 打开后群聊里必须 @ 机器人（或写出机器人昵称）才触发，`/wws` 也一样；私聊不受限 |

行为约定：

- 指令参数里的 **@ 群友会被忽略**：`@机器人 /wws 特殊绑定 cn 123456789 @某人` 等价于不带那个 @；
- 没启用过的口语词（例如默认配置下的「水表」）会交回聊天处理，不会被插件截走；
- `@群友 查 TA 水表` 是另一条路径，在 3.5「群聊 @ 查询」里单独开关。

### 3.3 战绩查询与渲染（`[wows]`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `wows_token` | 空（必填） | yuyuko API token，格式「QQ号:token」；**留空时除 `/wws help` 外的指令都会被拦截并提示获取方式**（见 2.4） |
| `use_browser` | `chromium` | 截图内核，`chromium` / `firefox`；chromium 性能约为 firefox 三倍 |
| `image_type` | `jpeg` | `jpeg` / `png` / `webp`；png/webp 体积更小但稍慢 |
| `user_image_cache_ttl_minutes` | `10080` | 头像/横幅本地缓存分钟数，`<=0` 永不过期 |
| `proxy_enabled` | `false` | 是否启用代理 |
| `proxy_url` | `http://127.0.0.1:7890` | 代理地址，仅启用时生效 |
| `http2_enabled` | `true` | 关掉就不需要 `h2`，但失去 HTTP/2 并发 |
| `local_test` | `false` | 调试模式：额外落盘渲染用 HTML |
| `save_template_html` | `false` | 出图时把 HTML 存到数据目录 `template_html/` |
| `render_error_fallback` | `false` | 渲染失败时退回纯文本错误（关闭则把报错画进图片） |

### 3.4 AI 对话查询（`[llm_tool]`，实验性）

这一组只影响「自然语言对话查询」，不影响 `/wws` 指令。**整组默认关闭**。

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 总开关；关闭后 AI 不再能调用战绩工具，只能走 `/wws` |
| `evaluate` | `true` | 开启后 AI 拿到数据会点评玩家表现；关闭则只转述数据 |
| `rate_limit` | `10` | 同一会话 60 秒内的工具调用上限，`<=0` 不限 |
| `require_at` | `true` | 群聊里只有 **@ 了机器人** 的那条消息才允许 AI 查战绩；私聊不受限，回复候选序号续查也不受限 |
| `enable_account` | `true` | 总表查询（`/wws 战绩` 的对话版） |
| `enable_ship` | `true` | 单船战绩 |
| `enable_recent` | `true` | 近期战绩（全部模式） |
| `enable_recent_random` | `true` | 近期随机战 |
| `enable_recent_rank` | `true` | 近期排位战 |
| `enable_ship_recent` | `true` | 单船近期战绩 |
| `enable_recent_battles` | `true` | 近期逐场战斗明细 |
| `enable_ships` | `true` | 按等级 / 国家 / 舰种筛选战舰 |
| `enable_roll_ship` | `true` | 随机抽一条已有战舰（娱乐） |
| `enable_bind` | `true` | 绑定游戏账号 |
| `enable_bind_list` | `true` | 查看已绑定账号 |
| `enable_bind_change` | `true` | 切换当前绑定账号 |

#### 模型要求（打开这一组之前先确认）

工具不是插件自己调用的，是交给宿主 MaiBot 的 **Planner（规划模型）** 调用的，
所以模型怎么配由 MaiBot 决定，插件里没有相关选项：

- **Planner 必须支持工具调用（function calling）**。在 WebUI「模型配置 → 模型角色」里把 `planner`
  指向一个支持工具调用的模型（配置文件为 `config/model_config.toml` 的 `[model_task_config.planner]`，
  官方注释就是「规划模型，决定麦麦的行动，需要有一定 Agent 能力的模型」）。
  Planner 不支持工具调用时，AI 只会陪你聊天、永远不调用工具，表现为「嘴上说帮我查，实际上什么都没查」
- **上下文别太小**：工具说明加上返回的精简 JSON 会占一段上下文，建议 32K 以上；
  上下文太紧时 Planner 容易漏掉工具或把数据截断，看起来就像「查不到」
- **12 个工具分两批给**：`wws_account` / `wws_ship` / `wws_recent` / `wws_ship_recent` / `wws_bind`
  一直可见；其余 7 个由 Planner 通过宿主的 `tool_search` 按需发现，不会用 `tool_search`
  也不影响最常见的几种查询
- **replyer 不参与取数**：写回复的模型看不到工具结果，所以数字一律以工具返回为准；
  「@机器人 查我的水表」这类由插件自己作答，不交给模型复述
- **不满足条件也不影响 `/wws`**：指令链路完全不经过模型，随时可用

可选（宿主侧，不是必需）：`tool_result_condense` 能把长结果先压缩再进上下文、
`planner_subagent` 让子代理在独立上下文里多轮取数，数据量大的账号可以打开。

### 3.5 群聊 @ 查询（`[mention_query]`，实验性）

群里 @ 一位群友、**同时叫到机器人**并提到水表，插件直接查**那位群友**绑定的账号并发图。
「叫到机器人」有两种写法，任选一种：

- 同时 @ 机器人：`@机器人 @群友 30天水表`
- 在文字里写出机器人的名字/别名：`@群友 小助手，查他水表`

**整组默认关闭**。要求叫到机器人，是为了避免聊天里「@某人 你战绩好烂」这种话把水表甩出来。

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 总开关；关闭后「@ 群友查水表」完全不触发 |
| `require_keyword` | `true` | 开启时消息里必须带触发词才查；关掉后只要 @ 群友就查，容易刷屏 |
| `keywords` | `水表,战绩,查一下,查查,查询` | 触发词，逗号分隔（中英文逗号、顿号都行），判断方式是「消息里包含」 |
| `rate_limit` | `3` | 同一个群 60 秒内最多触发几次，`<=0` 不限 |
| `require_bot_ref` | `true` | 还要求叫到机器人（@ 机器人，或文字里出现机器人的昵称/别名）；关掉就退回「@ 群友 + 关键词」即触发 |
| `llm_unbound_reply` | `false` | 实验性：被 @ 的人没绑定时，用宿主模型按人格现写一句提示（多一次 LLM 调用）；关闭时用插件内置固定文案 |

机器人的昵称与别名**直接读 MaiBot 自己的 `config/bot_config.toml`**（`[bot] nickname` 与
`alias_names`），插件里没有单独的名字配置；@ 机器人时聊天下发的群名片也会自动补进来。
改名字只改 MaiBot 配置即可，插件不用动。

触发后这条消息**不会进入 AI 对话**（插件自己回图，避免 AI 再复述一遍），也不会被过滤词 / 聊天上下文影响。
写的是 `/wws`（以及其它斜杠指令、已启用的指令词）时一律不抢，仍走原来的路径。

### 3.6 管理员指令

启动日志里会出现一次 32 位校验串，**把整条消息原样发给机器人（建议私信）**即可成为管理员：
成功后写数据目录下的 `admin.txt`（持久），并删除 `checkAdmin.txt`。管理员独有：

```
/wws 更新样式     /wws 更新战舰     /wws 检查更新
```

---

## 4. 使用方法

### 4.1 `/wws` 指令速查

发 `/wws help` 看完整列表。绑定族：

```
/wws bind cn 游戏昵称            昵称绑定（也可写「绑定 国服 昵称」）
/wws bind cn (First Last)        昵称含空格：整体用半角括号包住
/wws bind cn Alice　Smith        昵称含全角空格（U+3000）：自动查 AID 改走特殊绑定
/wws 特殊绑定 cn <AID>           用 AID 直接绑定（服务器不能省），完全跳过昵称搜索
/wws 查绑定                      绑定列表
/wws 切换绑定 [序号]              多账号切换，/wws 删除绑定 同理
@机器人 /wws xxx                 群聊里 @ 也可以
纯数字回复                       消费上一条查询的「选择序号」流程，超时自动退出
```

服务器关键词：`cn` / 国服、`asia` / 亚服、`eu` / 欧服、`na` / 美服、`ru` / 俄服。

### 4.2 账号绑定

| 场景 | 写法 | 说明 |
|---|---|---|
| 普通昵称 | `/wws bind cn 游戏昵称` | 昵称与服务器**都是必填** |
| 昵称带半角空格 | `/wws bind cn (First Last)` | 半角括号把多个词并成一个昵称；不加括号会只绑第一个词 |
| 昵称带全角空格 | `/wws bind cn Alice　Smith` | 插件按**原始字符串**查 AID，再改走 `特殊绑定 cn 123456789`，并切换默认账号 |
| 直接用 AID | `/wws 特殊绑定 cn <AID>` | 跳过昵称搜索，最稳；AID 在 `wows.shinoaki.com` 账号页可查 |
| 查看已绑定 | `/wws 查绑定` | 列出全部绑定与当前默认账号 |
| 切换 / 删除 | `/wws 切换绑定 2`、`/wws 删除绑定 2` | 一个 QQ 可绑多个号，查询始终用**默认账号** |

绑定前请注意：

- 上游昵称搜索是**精确匹配**，服务器或昵称错一个字都会失败（见 7.3）；
- 昵称含全角空格（U+3000）、NBSP、Tab 时，指令传输过程会把它们变成半角空格，普通 `bind` 必然失败——
  插件会自动改走 AID；查不到 AID 时直接回「没查到」，不会再拿半角空格去试；
- 参数不全（例如 `特殊绑定 123456789` 少了服务器）时，插件直接回正确用法，不再转发上游的「服务器名输入错误」。

### 4.3 唤起方式：三种写法

默认三种都可用，改 3.2 的配置可以收窄：

```
/wws 近期                    ← 斜杠指令（群聊、私聊都行）
@机器人 /wws 近期            ← 先 @ 机器人再写指令
@机器人 wws 近期             ← 只 @ 机器人 + 裸指令词
```

- 开启 `require_prefix` 后，群聊里裸写「wws …」不再触发；
- 开启 `require_at` 后，群聊里必须 @ 机器人（或写出它的昵称），连 `/wws` 也一样；
- 启用 `command_aliases = "wws,水表"` 后，`/水表 近期`、`@机器人 水表` 也能用；
- 指令里顺手 @ 了别人不会影响参数：`@机器人 /wws 特殊绑定 cn 123456789 @某人` 与不带 @ 等价
  （@ 的完整处理规则见 [4.7](#47-关于--提及的处理)）。

### 4.4 自然语言查询（AI，实验性）

> 先在 3.4 打开 `[llm_tool] enabled`；同时宿主的 **Planner 模型必须支持工具调用**
> （见 3.4「模型要求」）。只配了普通对话模型时会表现为「AI 说了要查但没查」，
> 这时换 `/wws` 指令即可——指令不走模型。

不必记指令，直接用日常说法提问，AI 会自己挑工具、取数、发图，再按 `evaluate` 开关决定是否点评：

```
查一下我的战绩
国服 Alice_01 最近一周打得怎么样
我这周排位战表现如何
看看我的北卡（单船）
我有哪些十级战列舰
随机抽一条我的船
帮我绑定国服 Alice_01 / 我绑了哪些号 / 换成第二个号
```

| 要点 | 说明 |
|---|---|
| 前提 | 「我的战绩」「随机抽船」「切换绑定」都依赖**已绑定账号**；没绑时 AI 会先引导绑定 |
| 查自己 | 插件用这条消息的发送者去 yuyuko 绑定表里解析账号（群会话宿主不传发送者，插件靠消息 Hook 自己记，600 秒内有效） |
| 查别人 | **服务器 + 昵称都要给**（只给昵称会变成 accountId 报错）；昵称含空格请整体加括号（如 `(First Last)`） |
| 时间说法 | 「今天」=1 天、「昨天」=2 天、「这周」=7 天、「这一个月」=30 天、「两个月」=60 天、「半年」=180 天、「一年」=360 天；没说时间又指定了船 = 查这艘船的**生涯总表**，没说时间也没指定船 = 最近 7 天 |
| 时间上限 | 近期窗口最大 366 天（yuyuko 接口的「最大查询时间一年」硬限制）。AI 对话与 @ 查询里超过 366 会夹到 366；`/wws 近期 400` 直接交给上游，由上游回「最大查询时间一年」 |
| 单船 + 时间 | 「今天西西里战绩」= 单船近期 1 天；「这一个月丰后」= 单船近期 30 天 |
| 空窗口 | 单船近期没有记录时会自动放宽到一年（366 天，yuyuko 接口上限）重查，并在说明里标注「已放宽」；仍然没有就如实说「没有出战记录」，不会拿整账号数据顶替 |
| 船名简写 | 原皮船可用别名 / 简写 / 英文名（「北卡」→ 北卡罗来纳、「彼得罗」「歪比」→ 彼得罗巴甫洛夫斯克、「z52」→ Z-52）；换皮船必须带标记（`北卡CLR`、`AL信浓`），没写标记只查原版 |
| 多候选 | 简称同时命中多条船时，按候选顺序取第一个有数据的，并说明「对上了 N 条船，先按 i 名字 给你看」 |
| 误触发防护 | 默认 `require_at = true`：群聊里只有 **@ 了机器人** 的消息才能查；私聊不需要 @ |
| 限流 | 默认每会话 60 秒 10 次（`rate_limit`） |

### 4.5 群里 @ 群友查水表（实验性）

> 先在 3.5 打开 `[mention_query] enabled`。

@ 一位群友、叫到机器人，再说一句水表 / 战绩：

```
@机器人 @张三 查水表
@张三 小助手，战绩怎么样
@机器人 @张三 彼得罗30天水表
```

| 情况 | 结果 |
|---|---|
| 张三绑过账号 | 先发「张三绑定的账号：国服 xxx」，再发水表图；说明与图同源，不会出现「说 A 图是 B」 |
| 张三没绑定 | 回「张三还没绑定账号」：默认固定文案；打开 `llm_unbound_reply` 后由宿主模型按人格现写（超时/失败仍回固定文案）。不提示去绑定，也不拿别人的数据顶替 |
| 消息同时 @ 了机器人 | 仍按「@ 群友」处理（被查的是那位群友，不是发送者） |
| 只有 @ 群友，没叫机器人 | 不触发（`require_bot_ref = true`）；聊天里的「@某人 你战绩好烂」不会甩出水表 |
| 写的是 `/wws ...` 或已启用的指令词 | 不抢，交给指令链 |
| 同一个群 60 秒内超过 `rate_limit` 次 | 静默跳过（只在日志里记一条） |

查的是**被 @ 的人自己设置的默认账号**：绑定关系存在 yuyuko 平台绑定表里（QQ 号 → 游戏账号），
插件按 QQ 号读取，不看群昵称；一个人绑了多个号时取**默认账号**。
查完这条消息不会进入 AI 对话，避免「水表图 + AI 复述」双份回应。

### 4.6 交互式选择序号

查询返回多个候选（例如简称同时命中多条船）时，插件会发候选图或编号列表，
直接**回复数字**即可继续，20 秒不选自动取消；不同会话互不影响。

### 4.7 关于 @ 提及的处理

hikari-core 从 v2 起**不再解析 @ 消息**，「谁被 @ 了」完全由接入端（本插件）负责。
遇到 @ 相关的问题，按下面三条规则理解：

| 场景 | 插件怎么处理 |
|---|---|
| 指令参数里夹着 @ | 一律剥掉。`@机器人 /wws 特殊绑定 cn 123456789 @某群友` 等同于不带那个 @；`@群友` 在参数里没有任何含义，留着会被当成参数挤掉 AID |
| `@机器人 wws xxx` | 先剥掉前缀 @，再按指令处理，与 `/wws xxx` 等价 |
| `@群友 水表`（人不是机器人） | 走 4.5 的「@ 查询」路径：以**被 @ 的人**的身份（`me` 模式）查 TA 绑定的默认账号，不会查成发送者 |

两种 @ 编码插件都认：MaiBot 的 `@<昵称:QQ号>`、纯文本 `@昵称`，以及适配器可能透传的
原始写法 `[CQ:at,qq=...]`、`<@!123>`。**原始 @ 码不会进入指令参数**——hikari 会把
`CQ:at,...` 当成普通参数，导致「服务器名输入错误」这类误报。

对应的内部约定（移植到其它平台时照这个来）：

1. 交给 hikari 前，先把 @ 转成等价的 `me` 语义——「@群友 单船 大和」= 以被 @ 者为
   `PlatformId`、指令文本为 `me 单船 大和`；
2. 查自己时 `me` 可省（`单船 大和`、`近期 30` 都会落到发送者），但**兜底总表查询必须显式写 `me`**，
   否则上游判成「未识别的指令」；
3. 绑定关系按 QQ 号从 yuyuko 绑定表读取，不看群昵称；一个 QQ 绑多个号时取默认账号。

---

## 5. 渲染依赖：浏览器内核与字体

### 5.1 自动安装

插件启动时会检查数据目录下的 `browsers/`，缺失/过期就自动执行 `playwright install chromium`，
下载源已内置为 `https://npmmirror.com/mirrors/playwright/`，完成后写缓存标记
`browsers/.chromium-env`（两行：`PLAYWRIGHT_BROWSERS_PATH=...`、`REVISION=1208`）。

- **Windows**：无需额外步骤，也不需要装 Linux 系统依赖
- **Docker / Linux**：安装脚本会再跑一次 `playwright install-deps`（需要 root 与网络），
  部分精简镜像会失败，此时手工补依赖或按下文预置内核

### 5.2 手工预置内核（网络差 / 离线）

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

### 5.3 中文字体

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

---

## 6. 日常维护

### 6.1 不用管的部分

| 项目 | 时机 | 来源 |
|---|---|---|
| 渲染模板 | 每天 04:00 / 12:00，另加每次启动 | `hikari-resource` OSS 的 `template-v2.json` |
| 战舰资源（新船、图标、中英文名） | 每周四 08:00 | yuyuko `nahida-static` |
| 战绩 / 公会 / 排行 | 每次指令实时请求 | yuyuko API |

WG 加新船、改数值都不需要人工干预；想立刻看到就 `/wws 更新战舰`（配合 `/wws 更新样式`）。

### 6.2 升级 hikari-core

> ⚠️ 内置的 `hikari_core/` **不再是纯上游副本**，本移植版打了三处安全补丁（见下表）。
> 整目录覆盖会一并抹掉它们，升级后必须按表把补丁重新应用。

| 文件 | 补丁 | 作用 |
|---|---|---|
| `hikari_core/features/system.py` | `check_version` 的自动同步 | 只在插件目录自身找 `.git`（不向上一级主程序目录查找），并把 origin 限定为本仓库 `byyh-5316/maibywowsyoyukobot`；origin 不符时直接拒绝（返回 -2），不执行任何 git 操作 |
| `hikari_core/features/system.py` | `_unsafe_zip_member` | 解压前校验 zip 条目：绝对路径、盘符、`../` 上跳、符号链接一律拒绝，防 zip-slip |
| `hikari_core/Html_Render/browser.py` | `playwright install-deps` | `os.system` 改为 `subprocess.run([...], check=False)`，不走 shell，失败不阻断内核安装 |

1. 备份：`cp -r hikari_core hikari_core.bak`
2. 从上游取新的 `hikari_core/` **整目录覆盖**，然后按上表重新打补丁（`wows_bot/` 适配层不受影响）
3. 重启麦麦；看日志「失败=0」，确认模板 / 战舰资源更新无报错
4. 冒烟：`/wws help`、`/wws clan cn ABCD`、`/wws 检查更新`（应不再触发越界同步）
5. 异常就回滚备份目录再重启

> ⚠️ **别信 `/wws 检查更新`**：它拉的上游 `version.json` 只登记到 `1.1.3`（2025-03-10），v2 线没登记，
> 而且是字符串比较，对内置的 `2.0.1` 永远回答「已经是最新版本了」。是否升级请看上游仓库
> `hikari_core/__init__.py` 里的 `__version__`。

### 6.3 备份清单

- 插件源码目录（**含填过 token 的 `config.toml`，备份等于备份凭据**）
- 数据目录里的 `browsers/`（重装最费时）、`ship_cache*`、`admin.txt`
- 截图产物 `file_img_temp/`、`browser_temp/` 可随手删

### 6.4 第三方域名清单

插件运行期会主动访问下列域名，全部为明文 HTTPS，不含任何遥测 / 统计：

| 域名 | 用途 | 触发时机 |
|---|---|---|
| `v3-api.wows.shinoaki.com` | yuyuko 战绩 API、头像 / 公会 / 排行图片、`nahida-static` 战舰资源 | 每次查询；每周四的资源更新 |
| `hikari-resource.oss-cn-shanghai.aliyuncs.com` | 模板清单 `template-v2.json` 与渲染模板文件 | 启动时、每天 04:00 / 12:00 |
| `benx1n.oss-cn-beijing.aliyuncs.com` | hikari 的 `version.json`，供 `/wws 检查更新` 比对版本 | 管理员手动执行该指令时 |
| `npmmirror.com` | Playwright 浏览器内核的国内下载镜像（`PLAYWRIGHT_DOWNLOAD_HOST`） | 首次安装 / 重建浏览器内核时 |
| `github.com` | 仅 `/wws 检查更新` 判定有新版本且安装目录是 git 仓库时，对 `origin` 执行 `git pull`（只允许本插件仓库，见 6.2） | 管理员手动执行该指令时 |
| `wows.mgaia.top` | **不发起请求**，只是 `/wws 特殊绑定` 提示里的地址（会 301 到 `wows.shinoaki.com`） | — |

如需在内网 / 代理环境部署，把前四个加入白名单即可正常查询；后两个只影响「检查更新」。

---

## 7. 排障

### 7.1 先看这两行日志

日志前缀 `[plugin.community.wows-yuyuko]`。每条 `/wws` 都会记录：

```
[wows] 指令 '<清洗后的参数>' user=<QQ> group=<群号>
[wows] 结果 status=success|error|wait server=<服务器> 昵称=<昵称> aid=<AID>
```

- `指令` 行是**参数清洗后**的文本：指令里 @ 了别人会被剥掉，群里看到的顺序可能与真实参数不同；
- `@ 查询触发` 行会打印 `target/name/类型/船/天数/自查/原文`，用来还原「@群友」路径；
- token 在日志里一律打码为 `***:***`。

### 7.2 常见问题对照表

| 现象 | 原因 | 处置 |
|---|---|---|
| 回复里带 `the 'h2' package is not installed` | 开了 http2 但缺 `h2` | 按 2.3 装 `requirements.txt`（或 `uv pip install h2`） |
| 一直回「正在准备渲染资源」 | 首次下载没完成 / 启动超 300 秒 | 看日志确认后台在推进；网络差就预置内核与战舰缓存（5.2） |
| 启动即 `FileNotFoundError: ship_cache_hash.json` | 上次下载被打断留下半截 `ship_cache.zip` | 已自愈：插件启动时校验 zip、补解压、重拉清单；仍失败就删数据目录里的 `ship_cache.zip` / `ship_cache/` / `ship_cache_hash.json` 再重启 |
| 找不到 chrome / `chrome_crashpad_handler` Permission denied | 内核缺失或权限不对（仅 Linux） | 见 5.2：`chmod 755` 目录 + `4755` `chrome_sandbox`，核对 `.chromium-env` 的 `REVISION` |
| playwright 升级后开始重装内核 | `browsers.json` 期望构建号变了 | 预置新 `chromium-<rev>/` 并改 `.chromium-env`，或把 playwright 降回 `1.58.0` |
| 「图片发送失败了」 | 通道权限 / 图片体积限制 | 确认机器人有发图权限；`image_type=jpeg` 体积最小 |
| 多人同时查询明显变慢 | 渲染是单浏览器串行（内置渲染锁） | 正常排队，不要去掉渲染锁 |
| 警告「模板清单里没有 nunjucks.min.js / hikari-render.js」 | 上游清单不带这两个 JS | 正常，渲染器随插件分发在 `hikari_core/Template/` 下 |
| 警告「未加载 data_user 私有模块…wws auth 将提示未部署」 | 上游可选私有模块没部署 | 正常，本插件不使用 `wws auth` |
| WebUI 插件详情读不出 README | 插件根目录缺 `README.md` | 确保 `plugins/MaiByWowsYoyukoBot/README.md` 存在（就是本文件） |
| 改完代码没生效 | 需要重载 / 重启 | 容器内改 `.py` 会自动重载；manifest / 依赖变更或 Windows 一键包请重启 |
| 插件列表里版本号还是旧的 | `_manifest.json` 没更新或未重启 | 改 `version` 后重启，必要时在插件页点「刷新」 |
| 回复「还没有配置 yuyuko API Token…」 | `wows_token` 为空：新装默认空，或在 WebUI 保存配置时 token 输入框没回填、被一起清空 | 按 2.4 重新粘贴到「API Token」保存即生效；改完别的选项后建议回来看一眼这一栏 |
| `/wws 近期` 回 `请求头不符合标准;uri=/api/wows/recent/day/info` | token 为空、格式不对（漏了 `QQ号:` 前缀）或前后带空格 | 必须是 `QQ号:token` 原样粘贴；改完保存再试 |
| 日志出现 `{'code': 409, ...}` 但水表能出图 | 上报数据接口要 token，空 token 时静默跳过 | 填好 token 后自动消失，不影响查询 |
| token 被限流 / 回鉴权类短错误 | 共用公开 token 或请求过频 | 用自己申请的 token；失效就重新申请，没有自助续期 |
| 群里发 `/wws` 没反应 | 插件未启用、被 3.2 的 `require_at` / `require_prefix` 拦住，或资源未就绪 | 看日志有没有 `[wows] 指令 ...`；确认 WebUI 的「唤起方式」设置 |
| 只 @ 了群友，插件却回了「服务器名输入错误」 | 旧版本把 @ 文本混进了指令参数 | 1.2.0 起已修：@ 会被剥掉，参数不全时回正确用法；升级后重启 |
| 发 `特殊绑定 <AID>` 被提示「只给 AID 不行」 | 特殊绑定要求「服务器 + AID」 | 发 `/wws 特殊绑定 cn <AID>`（服务器不能省） |
| 发 `bind cn 昵称` 一直「用户不存在」 | 昵称含全角空格 / NBSP，指令传输时变成了半角空格 | 直接发原昵称，插件会自动查 AID；或手动 `/wws 特殊绑定 cn <AID>` |
| 绑定成功但查出来还是旧账号 | 该账号不是默认账号 | `/wws 查绑定` 看默认，`/wws 切换绑定 <序号>` |
| 群里 @ 群友说水表没反应 | `[mention_query] enabled` 关闭、没叫到机器人、或没带关键词 | 见 3.5；确认 5 个开关与名字池 |
| 对话里说「查一下我的战绩」没反应 | `[llm_tool] enabled` 被关，或 Planner 模型不支持工具调用 / 没选中工具 | 先看 3.4「模型要求」：把 `planner` 换成支持 function calling 的模型；看日志有无工具调用记录；`/wws recent me` 正常说明数据链路没问题 |
| 对话查询回「Qwen/… does not support tools」之类报错 | 当前 `planner` 指向的模型不接受工具参数 | 换模型，或在「模型角色」里给 `planner` 加一个支持工具调用的备选；插件本身不用改 |
| AI 查询回「无法确定当前用户的平台账号…」 | 宿主没注入 `user_id`，或用户尚未绑定 | 先 `/wws bind cn 昵称`，再让 AI 重试 |
| AI 查询回「查询太频繁啦…」 | 触发了 `[llm_tool] rate_limit` | 等一分钟，或调大 / 设为 `0` |
| AI 复述的数字与实际不符 | 写回复的模型看不到工具结果（宿主限制） | 1.2.0 起「@机器人 查自己」走确定性路径，不经过模型；仍出现请反馈 |
| `/wws 检查更新` 永远说「已是最新」 | 上游 `version.json` 只登记到 1.x，且是字符串比较 | 别信它；有没有新版看仓库 Releases |
| `/wws 检查更新` 回「当前安装目录不是 git 仓库…」 | ZIP 安装，或插件目录没有 `.git` | 正常行为；按 2.6 手动 `git pull` 或覆盖安装 |
| `/wws 检查更新` 回「origin 不是本插件仓库…」 | `.git` 指向了别的仓库 | 正常行为，不会执行 git；确认 `git remote get-url origin` 是本插件地址 |
| 重启后管理员校验串变了 | 每次启动重新生成；已有 `admin.txt` 时不再生成 | 用最新日志里那条 32 位串私信机器人 |
| WebUI 插件页没有图标 | manifest 缺 `display.icon` 或文件丢失 | 确认 `_manifest.json` 的 `display.icon.value` 指向的文件存在，刷新页面 |
| Windows 启动时报 Playwright 下载失败 | 官方 CDN 不通 | 设 `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/`，或按 5.2 预置 |
| 换机器 / 重装后要重新下载约 400 MB | 数据目录没有迁移 | 迁移 `MaiMBot-plugin-data/community.wows-yuyuko`（见 6.3） |
| 日志「压缩包含越界条目，已拒绝解压」 | 模板 / 战舰 zip 含绝对路径、`../` 或符号链接 | 已拦截；删对应 zip 与解压目录后重试，反复出现就报 Issue |

### 7.3 「用户不存在」不是故障

yuyuko 的昵称搜索是**精确匹配**，实测结果：

| 输入 | 上游返回 |
|---|---|
| 完整真实昵称 + 正确服务器 | `code=200`，命中 1 条 |
| 只给前缀（想模糊搜） | `code=501`，上游超时——不支持模糊 |
| 同一昵称查错服务器 | `code=404 用户名不符合规范!` |
| 昵称尾部多一个空格 | `code=540` |
| 昵称写错 / 改过名 | `code=404 用户不存在` |

所以这句只会是**填错服务器或昵称**。插件已把上游原文替换为「实际使用的服务器 + 昵称 + 四步自查」的提示；
要立刻绕过就发 `/wws 特殊绑定 <服务器> <AID>`（例如 `/wws 特殊绑定 cn 123456789`，只给 AID 不行）。
昵称含全角空格时，插件会自动按原始字符串查 AID 再绑定，不需要你手动转换。

---

## 8. 卸载

1. WebUI 里停用插件（触发清理：停 APScheduler、关浏览器进程、清 `file_img_temp/`）；
2. 删源码目录 `plugins/MaiByWowsYoyukoBot`（市场安装则为 `plugins/community_wows-yuyuko`）；
3. 需要回收磁盘再删数据目录 `data/plugins/community.wows-yuyuko`
   （想重装就留着，`browsers/` 与 `ship_cache/` 都能直接复用）。

---

## 9. 出处与许可

本插件是 **AstrBot 插件在 MaiBot 上的移植版**，渲染核心与数据全部来自下列上游，版权归各原作者：

| 组件 | 作者 | 项目地址 |
|---|---|---|
| AstrByWowsYuyukoBot（原版 AstrBot 插件） | 西行寺雨季 | https://github.com/JustOneSummer/AstrByWowsYuyukoBot |
| hikari-core v2.0.1（内置渲染核心，MIT） | Hikari / benx1n | https://github.com/benx1n/HikariBot |
| yuyuko 战绩 API 与网页端 | yuyuko / shinoaki | https://v3-api.wows.shinoaki.com · https://wows.shinoaki.com |
| 渲染模板与静态资源 | hikari-resource | 插件启动时自动拉取 |

完整声明（含数据与商标说明）见 [NOTICE.md](NOTICE.md)，许可证见 [LICENSE](LICENSE)。

本仓库主要新增 MaiBot 适配层（`plugin.py`、`config.py`、`wows_bot/`、`yuyuko_llm/`）与部署文档；
`hikari_core/` 只有三处安全补丁，清单见 [6.2](#62-升级-hikari-core)。

> 原版仓库目前未声明开源许可证，本移植版按 MIT 发布并保留原作者完整署名；
> 如上游作者对分发方式有异议，请开 Issue 告知。

本移植版以 [MIT](LICENSE) 发布；战绩数据与图片样式归 yuyuko / Wargaming.net 所有，插件仅做接入展示。
