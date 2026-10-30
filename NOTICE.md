# 来源与致谢（NOTICE）

本插件是 **AstrBot 插件在 MaiBot 上的移植版**，随插件分发的渲染核心与数据均来自下列上游项目。
各上游代码、模板、图片与数据的版权归其各自作者所有；本仓库主要新增 MaiBot 适配层
（`plugin.py`、`config.py`、`wows_bot/`）与部署文档，`hikari_core/` 仅含 README 8.2 节列出的
两处本地安全补丁（自动同步范围限制、zip 条目校验、`playwright install-deps` 调用方式）。

## 上游项目

| 组件 | 作者 / 维护者 | 项目地址 | 说明 |
|---|---|---|---|
| AstrByWowsYuyukoBot（原版 AstrBot 插件） | 西行寺雨季 | https://github.com/JustOneSummer/AstrByWowsYuyukoBot | 本移植版的直接来源：指令语义、`yuyuko_bot/` 业务逻辑与整体交互均出自该项目 |
| hikari-core v2.0.1（渲染核心） | Hikari / benx1n | https://github.com/benx1n/HikariBot | 以源码形式内置于 `hikari_core/`，与本仓库一起分发；上游项目采用 MIT 协议 |
| yuyuko 战绩 API | yuyuko / shinoaki | https://v3-api.wows.shinoaki.com | 全部战绩、公会、排行数据由该服务实时提供 |
| yuyuko 战绩网页端 | yuyuko / shinoaki | https://wows.shinoaki.com | 查自己的 AID、生成「特殊绑定」指令 |
| 渲染模板与静态资源 | hikari-resource | https://hikari-resource.oss-cn-shanghai.aliyuncs.com/hikari_core_template/template-v2.json | 插件启动时自动拉取，不随仓库分发 |

> 说明：原版仓库 `AstrByWowsYuyukoBot` 目前未声明开源许可证；本移植版按 MIT 发布，
> 并保留原作者的完整署名。如上游作者对分发方式有异议，请开 Issue 告知，我们会及时调整。

## 运行期访问的第三方域名

插件不包含遥测，也不向上述作者以外的任何服务上报数据。运行期只访问：

| 域名 | 用途 |
|---|---|
| `v3-api.wows.shinoaki.com` | 战绩 API 与 `nahida-static` 战舰资源、头像 / 公会 / 排行图片 |
| `hikari-resource.oss-cn-shanghai.aliyuncs.com` | 模板清单与渲染模板 |
| `benx1n.oss-cn-beijing.aliyuncs.com` | `/wws 检查更新` 用的 `version.json` |
| `npmmirror.com` | Playwright 浏览器内核下载镜像（仅安装内核时） |
| `github.com` | 仅 `/wws 检查更新` 判定有新版本且插件目录是 git 仓库时对 `origin` 执行 `git pull`，且只允许本插件仓库 |

`wows.mgaia.top` 只出现在提示文案里，不会发起请求。

## 数据与商标

- 《战舰世界》(World of Warships) 相关名称、舰船数据与美术资源版权归 Wargaming.net 所有。
- 战绩数据由 yuyuko API 提供，本插件仅做展示，不缓存、不再分发原始数据。
- 本插件与 Wargaming.net、yuyuko、HikariBot 官方均无隶属关系。

## 申请 yuyuko API Token

加 QQ 群 **967546463**，按群公告提交申请，审核通过后 token 通过邮件下发，格式为
`<申请时使用的QQ号>:<邮件中的token>`。
