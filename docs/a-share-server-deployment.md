# MarketPrism 服务端部署指南

服务端提供账号授权、A 股公开数据缓存、研究任务与报告保存。默认普通客户端连接此服务，模型 Key 只需由服务端维护者配置。

## 本机启动

安装项目后，在启动终端设置以下变量；示例值必须替换为自己的配置。

```powershell
$env:AITRADE_AUTH_USERNAME = "admin"
$env:AITRADE_AUTH_PASSWORD = "<设置一个强密码>"
$env:AITRADE_AUTH_USERS_FILE = "$HOME/.aitrade/auth_users.json"
$env:AITRADE_DATA_CACHE_DIR = "$HOME/.aitrade/server_data_cache"
$env:AITRADE_RESULTS_DIR = "$HOME/.aitrade/results"
$env:AITRADE_LLM_PROVIDER = "custom_openai_compatible"
$env:AITRADE_CUSTOM_BASE_URL = "https://your-gateway.example.com/v1"
$env:AITRADE_CUSTOM_API_KEY = "<你的模型 API Key>"
$env:AITRADE_QUICK_THINK_LLM = "<快速模型名>"
$env:AITRADE_DEEP_THINK_LLM = "<深度模型名>"
python -m aitrade.server.auth_api
```

默认监听 `127.0.0.1:18080`。确认 `/health` 返回 `status: ok`，再打开 `/admin`，使用管理员账号创建普通用户。支持账号停用、密码更新与到期时间设置；当前不提供开放注册。

客户端启动前设置 `AITRADE_AUTH_SERVER_URL=http://127.0.0.1:18080`。远程部署时替换成自己的 HTTPS 域名。`AITRADE_ADVANCED_MODE=false` 为默认值，普通客户端不会显示模型 Key 和数据源高级配置。

## Linux 自托管

使用 Python 3.10+ 虚拟环境安装 `pip install -e .`。将配置存入受限权限的服务环境文件，使用 systemd 等进程管理器通过 `EnvironmentFile` 注入。账号文件、缓存和报告目录必须允许服务账号写入；不需要把应用进程设为 root。

启动命令示例（环境变量需提前注入）：

```bash
python -m uvicorn aitrade.server.auth_api:app --host 127.0.0.1 --port 18080 --workers 1
```

在前方配置 HTTPS 反向代理。事件流接口需要关闭代理缓冲并设置足够的读取超时。当前任务、部分会话状态存储于进程内，因此使用单进程；不要直接增加 workers 并假设任务状态自动共享。重启可能中断运行中的研究任务。

管理入口使用 HTTP Basic 认证，应仅通过 HTTPS 或本机可信环境访问。限制管理入口的网络访问，并妥善备份账号与报告目录。

## 数据缓存和预热

| 环境变量 | 默认值 / 用途 |
| --- | --- |
| `AITRADE_DATA_CACHE_DIR` | 默认 `~/.aitrade/server_data_cache`，服务端缓存目录 |
| `AITRADE_DATA_CACHE_TTL_SECONDS` | `86400`，缓存有效期（秒） |
| `AITRADE_PRECACHE_ENABLED` | 默认关闭，设为 `true` 开启后台预热 |
| `AITRADE_PRECACHE_SYMBOLS` | 可选，例如 `600519.SH,000001.SZ`，初始预热股票 |
| `AITRADE_PRECACHE_LOOKBACK_DAYS` | 默认 `120`，行情回看天数 |
| `AITRADE_PRECACHE_INTERVAL_SECONDS` | 默认 `86400`，预热间隔 |
| `AITRADE_PRECACHE_WATCHLIST_FILE` | 可选，覆盖服务端记忆股票列表路径 |

系统会记住分析过的 A 股代码，供预缓存使用。缓存与备用来源能降低接口偶发中断的影响，但无法保证数据实时、完整或持续可用。

## 关键接口

| 接口 | 用途 |
| --- | --- |
| `GET /health` | 健康检查 |
| `POST /auth/login` | 登录并获取令牌 |
| `POST /auth/verify` | 校验令牌与账号有效期 |
| `GET /admin` | 管理界面 |
| `/admin/api/users` | 账号管理 API，管理员认证 |
| `GET /data/stocks/search?q=...` | 授权后的股票搜索 |
| `POST /analysis/start` | 创建研究任务 |
| `GET /analysis/{id}/events` | 流式研究事件 |
| `GET /analysis/{id}/report` | 任务报告 |
| `GET /reports` | 已保存报告列表 |
| `GET /reports/{report_id}` | 读取报告 |

业务接口使用 Bearer Token，实际请求结构可查服务的 `/docs`。管理和模型密钥不应传给普通客户端。

## 配置与凭据

- `AITRADE_*` 名称为兼容历史配置保留，对外产品名为 MarketPrism。
- 有 python-dotenv 时会查找 `.env` 和 `.env.enterprise`，进程已有环境变量优先。
- `.env.example` 中所有密钥值为空，仅作模板。不要提交真实配置、账号数据库或桌面会话文件。
- 桌面配置默认位于 `~/.aitrade/desktop_config.json`，可能覆盖环境变量提供的部分配置。排查时先确认实际配置来源。
- 使用云模型会向所选服务发送研究输入；请自行选择符合数据要求的模型服务。

## 常见问题

**提示登录服务未配置**：在启动本地桌面 API 的环境设置 `AITRADE_AUTH_SERVER_URL`，并检查已有桌面配置。

**管理员无法登录**：确认运行服务的进程收到非空 `AITRADE_AUTH_PASSWORD`，并核对账号及服务地址。

**登录后数据失败**：检查服务器网络、数据源限制、缓存目录权限和账号是否到期。先检查 `/health`，再查看脱敏后的服务日志。

**模型报错**：核对网关是否包含正确的 API 路径、模型名是否可用、余额和 Key 是否有效。不要将完整错误请求中的凭据发到公开 Issue。

**浏览器有界面但后端未就绪**：浏览器开发模式需要单独启动 `npm run desktop:api`；Tauri 模式由桌面壳管理本地后端。

**Docker 用法**：现有 Dockerfile / Compose 默认入口是 CLI `aitrade`，并非开箱即用的授权服务器部署。服务端容器部署需要自行覆盖启动命令、环境变量、挂载目录和端口；不得将本地凭据复制到镜像内。
