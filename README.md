# Grok 注册台

独立的 Grok/xAI 协议注册管理台，只包含注册、账号结果、CPA/OIDC、导出和 Grok2API 远端入池能力。运行时只依赖 FastAPI、React 和 SQLite，不需要 Go、Redis 或 PostgreSQL。

## 功能边界

- 协议注册批次、并发限制、停止和失败日志
- 管理员初始化与 `HttpOnly` 会话
- SQLite 持久化，密码、SSO、OAuth 和外部服务密钥加密存储
- `tokens.txt`、`accounts.txt`、`cpa_auths.zip` 下载
- 手动生成 CPA/OIDC
- 独立的 SSO、Build 和 Console 远端入池
- 远端入池复用进程级管理员会话，Access Token 到期后优先使用 Refresh Cookie 刷新
- 中断任务恢复为 `interrupted`，不会在重启后自动重复注册

远端入池和 CPA/OIDC 是独立操作。它们失败时不会改变已经成功的注册状态。

## Windows

要求 Python 3.11+ 和 Node.js 20+。

1. 运行 `启动Solver.bat`，等待本地 Turnstile Solver 就绪。
2. 运行 `启动Web.bat`。
3. 打开 `http://127.0.0.1:18081`，首次访问时创建管理员。

开发前端可运行：

```powershell
.\启动Web.ps1 -Dev
```

开发页面地址为 `http://127.0.0.1:5173`。

## Docker/Linux

```bash
cp .env.example .env
docker compose up -d --build
```

打开 `http://127.0.0.1:18080`。Compose 会同时运行两线程 Turnstile Solver，并将应用数据保存到命名卷 `registration-data`。

本地非 Docker 启动：

```bash
chmod +x start.sh turnstile-solver/start.sh
./turnstile-solver/start.sh
./start.sh
```

## 配置

注册和远端服务参数在登录后的“系统设置”中维护。敏感字段不会回传浏览器；已经配置的密钥留空保存时会保留原值。

- 本地 Solver URL：Windows 默认 `http://127.0.0.1:5072`；Compose 自动使用 `http://solver:5072`
- 注册并发：最多 50；使用本地 Solver 时会按 Solver 能力进一步限制
- 远端操作并发：默认 4、最多 50，仅限制 SSO、Build 和 Console 入池，不占用本地账号操作槽位
- 自动重试：只重试 `CreateUser` 前失败，避免可能已创建账号后重复注册
- CPA 远端模式既接受站点 Base URL，也接受完整 `/api/admin/v1/accounts/import` 地址

生产 HTTPS 部署应设置：

```dotenv
REG_CONSOLE_COOKIE_SECURE=1
```

请持久化并备份 `data/.secret-key` 与 SQLite 数据库。丢失该密钥后，已加密的账号和服务凭据无法恢复。

## 静态检查

交付不包含 mock 或离线测试。需要检查源码时可运行：

```bash
python -m compileall -q app grok2api scripts
npm --prefix frontend run typecheck
npm --prefix frontend run lint
```

生产前端构建：

```bash
npm --prefix frontend run build
```