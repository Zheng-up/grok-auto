from __future__ import annotations

import os
import socket
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.getenv("REG_CONSOLE_DATA_DIR", ROOT / "data")).resolve()
DB_PATH = Path(os.getenv("REG_CONSOLE_DB", DATA_DIR / "registration.db")).resolve()
SECRET_KEY_PATH = Path(os.getenv("REG_CONSOLE_SECRET_KEY", DATA_DIR / ".secret-key")).resolve()
EXPORT_DIR = Path(os.getenv("REG_CONSOLE_EXPORT_DIR", DATA_DIR / "exports")).resolve()
CPA_AUTH_DIR = Path(os.getenv("REG_CONSOLE_CPA_DIR", DATA_DIR / "cpa_auths")).resolve()
FRONTEND_DIST = Path(os.getenv("REG_CONSOLE_FRONTEND", ROOT / "frontend" / "dist")).resolve()

# 首选端口被占用时，最多向后探测的端口数（含首选本身）
PORT_PROBE_LIMIT = max(1, int(os.getenv("REG_CONSOLE_PORT_PROBE_LIMIT", "50")))


@dataclass(frozen=True)
class RuntimeConfig:
    host: str = os.getenv("REG_CONSOLE_HOST", "0.0.0.0")
    port: int = int(os.getenv("REG_CONSOLE_PORT", "18080"))
    cookie_secure: bool = os.getenv("REG_CONSOLE_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"}
    session_hours: int = max(1, int(os.getenv("REG_CONSOLE_SESSION_HOURS", "24")))
    registration_max_concurrency: int = 50
    local_solver_max_concurrency: int = 50


runtime = RuntimeConfig()


def ensure_runtime_dirs() -> None:
    for path in (DATA_DIR, EXPORT_DIR, CPA_AUTH_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _bind_targets(host: str) -> list[str]:
    """浏览器常走 127.0.0.1；0.0.0.0 监听时也要确认环回未被其它进程独占。"""
    normalized = (host or "0.0.0.0").strip() or "0.0.0.0"
    if normalized in {"0.0.0.0", "::", "[::]"}:
        return ["0.0.0.0", "127.0.0.1"]
    return [normalized]


def is_port_free(host: str, port: int) -> bool:
    """不设 SO_REUSEADDR，避免 Windows 上误判“可复用即空闲”。"""
    if port <= 0 or port > 65535:
        return False
    for target in _bind_targets(host):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind((target, port))
        except OSError:
            return False
    return True


def resolve_listen_port(host: str, preferred: int, limit: int = PORT_PROBE_LIMIT) -> int:
    """从 preferred 起逐个 +1，直到找到可绑定端口。"""
    start = max(1, min(preferred, 65535))
    end = min(65535, start + max(1, limit) - 1)
    for port in range(start, end + 1):
        if is_port_free(host, port):
            return port
    raise RuntimeError(
        f"no free port in range {start}-{end} for host={host!r} "
        f"(preferred={preferred}, probe_limit={limit})"
    )


def apply_listen_port(port: int) -> RuntimeConfig:
    """启动选定端口后回写全局 runtime，供日志与其它模块读取。"""
    global runtime
    runtime = replace(runtime, port=port)
    return runtime


def public_base_url(host: str | None = None, port: int | None = None) -> str:
    """给人看的访问地址（0.0.0.0 / :: 显示为 127.0.0.1）。"""
    listen_host = host if host is not None else runtime.host
    listen_port = port if port is not None else runtime.port
    display = listen_host.strip() if listen_host else "127.0.0.1"
    if display in {"0.0.0.0", "::", "[::]", ""}:
        display = "127.0.0.1"
    return f"http://{display}:{listen_port}"
