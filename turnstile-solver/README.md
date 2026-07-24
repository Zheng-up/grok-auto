# turnstile-solver

`grok-auto`（Grok 注册台）的本地 Cloudflare Turnstile 过盾组件，默认使用 **Camoufox**。

当前随注册台 **Compose 独立容器** 运行（服务名 `solver`，内网 `http://solver:5072`），也可在 Windows / Linux 宿主机单独启动。

## Compose（推荐）

根目录：

```bash
docker compose up -d --build
```

注册台通过环境变量连接 Solver：

```env
REG_CONSOLE_SOLVER_URL=http://solver:5072
```

常用 Solver 环境变量：

```env
TURNSTILE_HOST=0.0.0.0
TURNSTILE_PORT=5072
TURNSTILE_THREAD=1
TURNSTILE_BROWSER_TYPE=camoufox
TURNSTILE_PREFETCH_TTL=75
TURNSTILE_IDLE_SEC=60
TURNSTILE_PREFETCH_IDLE_SEC=90
TURNSTILE_BROWSER_RECYCLE_EVERY=40
TURNSTILE_LAZY=1
SOLVER_MEMORY=3g
```

说明：

- `TURNSTILE_LAZY=1`：无任务时不常驻重型浏览器，按需启动
- `TURNSTILE_PREFETCH_TTL`：预取 token 有效期
- `TURNSTILE_BROWSER_RECYCLE_EVERY`：处理 N 次后回收浏览器，抑制 Camoufox 内存增长
- `SOLVER_MEMORY`：Compose 内存上限（默认 3g）

## 协议

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/createTask` | 创建过盾任务 |
| POST | `/getTaskResult` | 查询结果 |
| POST | `/prefetch` | 设置/清理预取深度（注册台按并发预热） |

注册台开跑批次时会：

1. 预热 Camoufox
2. 按注册并发设置 prefetch depth
3. 无活跃注册时将 depth 清零，释放预取压力

## Windows / 本地调试

```bat
TurnstileSolver.bat
```

或：

```bash
./start.sh
./stop.sh
```

默认监听：`http://127.0.0.1:5072`。

## 日志

```bash
docker logs -f grok-auto-solver
# 或容器内
docker exec grok-auto-solver tail -n 100 /app/logs/turnstile_solver.log
```

## 与旧文档差异

旧版描述为嵌在 `grokcli-2api` 主容器内的 inline solver；当前 **grok-auto** 部署为独立 `grok-auto-solver` 容器，通过 Docker 网络别名 `solver` 访问。
