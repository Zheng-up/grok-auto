from __future__ import annotations

import os
from dataclasses import dataclass
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