# 更新日志

## 1.0.2

- 不再内置任何第三方 API Token：`wows_token` 默认空，由使用者自行申请
- 未配置 token 时拦截指令并回复获取渠道（QQ 群 967546463）、填写位置与 README 指引，不再把上游的 `请求头不符合标准` 抛给用户；`/wws help` 仍可用
- 启动日志在 token 为空时给出一条明确告警，便于排障
- README 排障表补全：空 token、token 格式错误、409 上报失败、限流/封禁、图标缺失、`/wws 检查更新` 不可信、管理员校验串、Playwright 下载失败、数据目录迁移

## 1.0.1

仅新增插件图标，功能与 1.0.0 完全一致。

- 新增 `assets/icon.png`，通过 `_manifest.json` 的 `display.icon` 声明，用于 WebUI 插件页与插件市场展示

## 1.0.0

首个 MaiBot 版本。

- 移植 AstrBot 插件 AstrByWowsYuyukoBot 到 MaiBot 插件 SDK v2，指令语义与原版保持一致
- `/wws <参数>` 覆盖帮助、绑定、单船、近期、公会、排行等全部子指令，渲染 yuyuko 水表图片
- 交互式「回复序号选择」流程（20 秒超时），按会话隔离，互不串台
- 内置 hikari-core v2.0.1 源码，模板与战舰资源按上游节奏自动更新（模板每天 04:00/12:00，战舰资源每周四 08:00）
- 启动自愈：修复被中断下载的 `ship_cache.zip`、补解压、缺失清单自动补拉
- 昵称查不到时回显实际使用的服务器与昵称，并给出四步自查与 `/wws 特殊绑定 <AID>` 兜底
- 渲染结果以 base64 直接发图，不落盘；卸载时停调度器、关浏览器、清临时产物
- 适配 Windows（一键包 / 裸 Python）与 Docker（Linux）两种部署方式
- 插件目录名与仓库名一致（`MaiByWowsYoyukoBot`，全英文），插件类型声明为 `other`
