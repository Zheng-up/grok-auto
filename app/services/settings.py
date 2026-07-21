from __future__ import annotations

import json
import os
from typing import Any

from app.crypto import SecretBox
from app.db import Database

DEFAULTS: dict[str, Any] = {
    "mail_provider": "cfmail",
    "mail_api_key": "",
    "mail_base_url": "",
    "mail_domains": "",
    "captcha_provider": "local",
    "captcha_api_key": "",
    "local_solver_url": os.getenv("REG_CONSOLE_SOLVER_URL", "http://127.0.0.1:5072"),
    "proxy_pool": "",
    "proxy_strategy": "round_robin",
    "registration_count": 1,
    "registration_concurrency": 2,
    "registration_retry_limit": 1,
    "mail_poll_timeout": 180,
    "remote_operation_concurrency": 4,
    "oidc_auto_mint": True,
    "remote_web_auto_push": False,
    "remote_build_auto_push": False,
    "remote_console_auto_push": False,
    "remote_base_url": "",
    "remote_username": "admin",
    "remote_secret": "",
}
SECRET_KEYS = {"mail_api_key", "captcha_api_key", "proxy_pool", "remote_secret"}
INT_RANGES = {
    "registration_count": (1, 25000),
    "registration_concurrency": (1, 50),
    "registration_retry_limit": (0, 5),
    "mail_poll_timeout": (30, 600),
    "remote_operation_concurrency": (1, 10),
}
CHOICES = {
    "mail_provider": {"cfmail", "moemail", "yyds", "gptmail", "tempmail"},
    "captcha_provider": {"local", "yescaptcha"},
    "proxy_strategy": {"round_robin", "random"},
}
BOOL_KEYS = {
    "oidc_auto_mint",
    "remote_web_auto_push",
    "remote_build_auto_push",
    "remote_console_auto_push",
}


def _normalize_value(key: str, value: Any) -> Any:
    if key in INT_RANGES:
        low, high = INT_RANGES[key]
        number = int(value)
        if not low <= number <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        return number
    if key in BOOL_KEYS:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean")
        return value
    if key in CHOICES:
        normalized = str(value or "").strip().lower()
        if normalized not in CHOICES[key]:
            raise ValueError(f"unsupported {key}")
        return normalized
    if isinstance(value, (dict, list)):
        raise ValueError(f"{key} must be a scalar value")
    normalized = str(value or "").strip()
    if len(normalized) > 20_000:
        raise ValueError(f"{key} is too long")
    return normalized


class SettingsService:
    def __init__(self, db: Database, box: SecretBox):
        self.db = db
        self.box = box

    def _decode(self, row: dict[str, Any]) -> Any:
        raw = self.box.decrypt(row["value"]) if row.get("is_secret") else row["value"]
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.fetch_one("SELECT * FROM settings WHERE key=?", (key,))
        if row:
            return self._decode(row)
        return DEFAULTS.get(key, default)

    def get_all(self, *, reveal_secrets: bool = False) -> dict[str, Any]:
        rows = {row["key"]: row for row in self.db.fetch_all("SELECT * FROM settings")}
        result: dict[str, Any] = {}
        for key, default in DEFAULTS.items():
            row = rows.get(key)
            if key in SECRET_KEYS and not reveal_secrets:
                configured = bool(self._decode(row)) if row else bool(default)
                result[key] = ""
                result[f"{key}_configured"] = configured
            else:
                result[key] = self._decode(row) if row else default
        return result

    def set_many(self, values: dict[str, Any], *, keep_empty_secrets: bool = True) -> dict[str, Any]:
        for key, value in values.items():
            if key not in DEFAULTS:
                continue
            if key in SECRET_KEYS and keep_empty_secrets and (value is None or str(value).strip() == ""):
                continue
            value = _normalize_value(key, value)
            raw = json.dumps(value, ensure_ascii=False)
            secret = key in SECRET_KEYS
            stored = self.box.encrypt(raw) if secret else raw
            self.db.execute(
                "INSERT INTO settings(key,value,is_secret,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value,is_secret=excluded.is_secret,updated_at=CURRENT_TIMESTAMP",
                (key, stored, int(secret)),
            )
        return self.get_all(reveal_secrets=True)

    def registration_config(self) -> dict[str, Any]:
        return self.get_all(reveal_secrets=True)