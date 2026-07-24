# Grok 注册台

独立的 Grok/xAI 协议注册管理台：注册、账号结果、任务队列、CPA/OIDC、导出，以及 Grok2API 远端入池。  
运行时依赖 **FastAPI + React + SQLite**，不需要 Go、Redis 或 PostgreSQL。

当前版本：**v1.1.0**

线上部署示例：`https://grokcli.zradx.com`（OpenResty TLS → `127.0.0.1:18080`）。

## 功能边界

### 注册

- 协议注册批次：创建、暂停、继续、停止、失败重试
- **全局账号槽位队列**：多批次共享并发上限，旧批次排队任务优先占槽
- 批次状态：`queued` / `running` / `waiting` / `pausing` / `paused` / `stopping` / `interrupted` 等
- 自动重试：仅重试 `CreateUser` 前失败，避免账号可能已创建后的重复注册
- 进程重启后中断任务标记为 `interrupted`，**不会**自动继续注册
- 本地 Turnstile Solver：开跑前按并发 **预热 Camoufox + prefetch**；无活跃注册时清理 prefetch，降低内存占用

### 账号与任务

- 账号列表：筛选 SSO / Build / Console 入池状态，批量操作
- 任务日志：注册批次 + 账号操作统一查看，支持暂停/继续/停止/重试
- 全局任务状态条：活跃注册与远端操作进度
- 本地操作（OIDC 生成）与远端入池分池并发，互不抢占本地账号槽位
- 失败任务可单项重试、等待中任务可重试、批量重试失败任务

### 导出与远端

- 下载：`tokens.txt`、`accounts.txt`、`cpa_auths.zip`
- 手动生成 CPA/OIDC（auths）
- 独立远端入池：SSO（Web）、Build（CPA）、Console
- 远端会话复用进程级管理员登录；Access Token 到期优先 Refresh Cookie 刷新
- 可测连通、查看远端会话状态、强制刷新会话
- 可选自动推送：`remote_web_auto_push` / `remote_build_auto_push` / `remote_console_auto_push`（默认关闭）
- 远端入池 / CPA 失败**不会**改写已成功的注册状态

### 安全与存储

- 管理员初始化与 `HttpOnly` 会话 Cookie
- SQLite 持久化；密码、SSO、OAuth、外部密钥加密存储
- 敏感设置不下发明文；已配置密钥留空保存时保留原值
- 请持久化并备份 `data/.secret-key` 与 SQLite；密钥丢失后已加密凭据无法恢复

## 页面

| 路径 | 说明 |
|------|------|
| `/` | 开始注册 |
| `/accounts` | 账号管理 |
| `/tasks` | 任务日志 |
| `/settings` | 系统设置 |

界面支持桌面 / 移动布局与系统主题切换。

## Windows

要求 **Python 3.11+**、**Node.js 20+**。

1. 运行 `启动Solver.bat`，等待本地 Turnstile Solver 就绪。
2. 运行 `启动Web.bat`。
3. 打开 `http://127.0.0.1:18081`，首次访问创建管理员。

开发前端：

```powershell
.\启动Web.ps1 -Dev
```

开发页：`http://127.0.0.1:5173`。

## Docker / Linux

```bash
cp .env.example .env
# 生产 HTTPS 反代后建议：
# REG_CONSOLE_COOKIE_SECURE=1
docker compose up -d --build
```

默认监听 `http://127.0.0.1:18080`。Compose 会启动：

- `console`：注册台（`grok-auto`）
- `solver`：独立 Turnstile Solver（Camoufox，默认懒启动 + prefetch TTL）

数据目录请挂载宿主机路径（示例部署使用 `/opt/grok-auto/data`）。

本地非 Docker：

```bash
chmod +x start.sh turnstile-solver/start.sh
./turnstile-solver/start.sh
./start.sh
```

## 配置

### 环境变量（`.env`）

| 变量 | 说明 | 默认 |
|------|------|------|
| `REG_CONSOLE_HOST` / `PORT` | 监听地址 | `0.0.0.0` / `18080` |
| `REG_CONSOLE_COOKIE_SECURE` | HTTPS 下 Cookie Secure | `0`（Compose 生产常用 `1`） |
| `REG_CONSOLE_SESSION_HOURS` | 会话时长 | `24` |
| `REG_CONSOLE_SOLVER_URL` | 本地 Solver 地址 | `http://127.0.0.1:5072`；Compose 内为 `http://solver:5072` |
| `TURNSTILE_THREAD` | Solver 浏览器线程 | `1` |
| `TURNSTILE_PREFETCH_TTL` | 预取 token TTL（秒） | `75` |
| `TURNSTILE_LAZY` | 懒启动浏览器 | `1` |
| `SOLVER_MEMORY` | Solver 容器内存上限 | `3g` |

### 系统设置（登录后 UI）

- 邮箱：`cfmail` / `moemail` / `yyds` / `gptmail` / `tempmail`
- 验证码：`local`（推荐）或 `yescaptcha`
- 代理池与 `round_robin` / `random` 策略
- 注册数量（1–25000）、注册并发（1–50，受 Solver 能力再限）、注册重试、邮箱轮询超时
- 本地操作并发、操作重试、**远端入池并发**（默认 2，最大 50）
- OIDC 自动生成、远端 Web/Build/Console 自动入池开关
- 远端 Base URL（支持站点根或完整 import 路径）、用户名与密钥

## Turnstile Solver

详见 [`turnstile-solver/README.md`](./turnstile-solver/README.md)。

要点：

- 协议：`POST /createTask`、`POST /getTaskResult`、`POST /prefetch`
- 注册开跑时按并发预热并设置 prefetch depth
- 回收浏览器 / 限制内存增长，避免 Camoufox 常驻膨胀
- 与注册台分容器运行（Compose），通过内网别名 `solver` 访问

## 运维建议

- 生产务必 `REG_CONSOLE_COOKIE_SECURE=1`，并由反向代理终止 TLS
- 定期备份 `data/`（至少 `registration.db` + `.secret-key`）
- Solver 内存不足时提高 `SOLVER_MEMORY` 或降低注册并发 / `TURNSTILE_THREAD`
- 健康检查：`GET /health`

## 静态检查与构建

交付不含 mock 测试。可运行：

```bash
python -m compileall -q app grok2api scripts
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run build
```

一键重建并启动（部署机）：

```bash
./scripts/rebuild-and-run.sh
```

## 版本记录

### v1.1.0

- 注册批次全局排队与暂停/继续/停止/重试
- 任务中心与全局任务状态 UI（含移动端布局）
- Turnstile prefetch / 懒启动与 Camoufox 内存回收
- 远端会话生命周期修复、失败任务重试与等待重试
- 独立远端操作并发与自动入池开关
- Solver 与 Console 分容器 Compose 部署说明

### v1.0.0

- 首个独立发布：本地注册 + 导出 + Grok2API 远端入池
