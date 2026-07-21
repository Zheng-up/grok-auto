from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from app.config import CPA_AUTH_DIR
from app.redaction import redact_error
from app.services.accounts import AccountService
from app.services.events import EventLog
from app.services.settings import SettingsService

_IMPORT_PATHS = {
    "build": "/api/admin/v1/accounts/import",
    "web": "/api/admin/v1/accounts/web/import",
    "console": "/api/admin/v1/accounts/console/import",
}
_MODE_ALIASES = {"cpa": "build", "sso": "web"}
_STATUS_FIELDS = {
    "build": "remote_build_status",
    "web": "remote_web_status",
    "console": "remote_console_status",
}
_MODE_LABELS = {"build": "Build 入池", "web": "SSO 入池", "console": "Console 入池"}
_REMOTE_COOLDOWN_SECONDS = 30.0
_REMOTE_COOLDOWN = threading.Condition()
_REMOTE_COOLDOWN_UNTIL = 0.0
_REMOTE_SLOTS = threading.Condition()
_REMOTE_ACTIVE = 0
_REMOTE_ACCESS_TTL_SECONDS = 14 * 60.0
_REMOTE_LOGIN_COOLDOWN_SECONDS = 65


class RemoteRateLimitedError(RuntimeError):
    remote_rate_limited = True


def _is_rate_limited(exc: Exception) -> bool:
    if bool(getattr(exc, "remote_rate_limited", False)):
        return True
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    return bool(re.search(r"(?<!\d)429(?!\d)", message)) or any(
        marker in message for marker in ("too many requests", "rate limit", "rate_limited")
    )


def _rate_limit_cooldown(exc: Exception) -> int:
    explicit = getattr(exc, "remote_cooldown_seconds", None)
    if explicit is not None:
        return max(1, int(explicit))
    response = getattr(exc, "response", None)
    request = getattr(response, "request", None)
    path = str(getattr(getattr(request, "url", None), "path", ""))
    return 65 if path.endswith("/api/admin/v1/auth/login") else int(_REMOTE_COOLDOWN_SECONDS)


def _normalize_sso(value: str) -> str:
    token = str(value or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token.split(";", 1)[0].strip()


def _admin_origin(base: str) -> str:
    normalized = base.strip().rstrip("/")
    lowered = normalized.lower()
    known_paths = [
        "/api/admin/v1/accounts/console/import",
        "/api/admin/v1/accounts/web/import",
        "/api/admin/v1/accounts/import",
        "/api/admin/v1/auth/login",
    ]
    for path in known_paths:
        marker = lowered.find(path)
        if marker >= 0:
            return normalized[:marker]
    return normalized


def _parse_sse(response: httpx.Response) -> dict[str, Any]:
    content_type = str(response.headers.get("content-type") or "").lower()
    if "text/event-stream" not in content_type:
        raise RuntimeError("远端导入接口未返回 SSE 事件流")
    complete: dict[str, Any] | None = None
    for block in re.split(r"\r?\n\r?\n", response.text or ""):
        event = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if not data_lines:
            continue
        raw = "\n".join(data_lines)
        try:
            payload: Any = json.loads(raw)
        except Exception:
            payload = raw
        if event == "error":
            message = payload.get("message") if isinstance(payload, dict) else payload
            raise RuntimeError(f"远端导入失败：{message}")
        if event == "complete" and isinstance(payload, dict):
            complete = payload
    if complete is None:
        raise RuntimeError("远端导入未返回 complete 事件")
    completed = sum(int(complete.get(key) or 0) for key in ("created", "updated", "synced"))
    sync_failed = int(complete.get("syncFailed") or 0)
    if completed <= 0 and sync_failed > 0:
        raise RuntimeError(f"远端账号同步失败：syncFailed={sync_failed}")
    return complete


class RemoteAdminSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=30, follow_redirects=True)
        self._identity: tuple[str, str, str] | None = None
        self._access_token = ""
        self._access_expires_at = 0.0
        self._login_blocked_until = 0.0

    def _reset(self, identity: tuple[str, str, str]) -> None:
        self._identity = identity
        self._access_token = ""
        self._access_expires_at = 0.0
        self._login_blocked_until = 0.0
        self._client.cookies.clear()

    @staticmethod
    def _token(payload: Any) -> str:
        data = payload.get("data") if isinstance(payload, dict) else {}
        tokens = data.get("tokens") if isinstance(data, dict) else {}
        access = str(tokens.get("accessToken") or "") if isinstance(tokens, dict) else ""
        if not access:
            raise RuntimeError("远端认证响应缺少 accessToken")
        return access

    def _login(self, origin: str, username: str, secret: str) -> str:
        remaining = self._login_blocked_until - time.monotonic()
        if remaining > 0:
            exc = RemoteRateLimitedError(
                f"管理员登录仍在限流窗口，等待 {max(1, round(remaining))} 秒后重试"
            )
            exc.remote_cooldown_seconds = max(1, round(remaining))
            raise exc
        response = self._client.post(
            f"{origin}/api/admin/v1/auth/login",
            json={"username": username, "password": secret},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if response.status_code == 429:
                self._login_blocked_until = (
                    time.monotonic() + _REMOTE_LOGIN_COOLDOWN_SECONDS
                )
                exc.remote_cooldown_seconds = _REMOTE_LOGIN_COOLDOWN_SECONDS
            raise
        self._login_blocked_until = 0.0
        return self._token(response.json())

    def _refresh(self, origin: str) -> str | None:
        response = self._client.post(
            f"{origin}/api/admin/v1/auth/refresh",
            json={},
        )
        if response.status_code == 401:
            return None
        response.raise_for_status()
        return self._token(response.json())

    def access_token(
        self,
        cfg: dict[str, Any],
        *,
        force_refresh: bool = False,
        stale_token: str = "",
    ) -> tuple[str, str]:
        origin = _admin_origin(str(cfg["remote_base_url"]))
        username = str(cfg.get("remote_username") or "admin")
        secret = str(cfg["remote_secret"])
        identity = (origin, username, secret)
        with self._lock:
            if self._identity != identity:
                self._reset(identity)
            now = time.monotonic()
            if force_refresh and stale_token and self._access_token != stale_token and self._access_token:
                return origin, self._access_token
            if self._access_token and not force_refresh and now < self._access_expires_at:
                return origin, self._access_token
            access = self._refresh(origin) if self._client.cookies else None
            if not access:
                access = self._login(origin, username, secret)
            self._access_token = access
            self._access_expires_at = time.monotonic() + _REMOTE_ACCESS_TTL_SECONDS
            return origin, access

    def close(self) -> None:
        with self._lock:
            self._client.close()
            self._identity = None
            self._access_token = ""
            self._access_expires_at = 0.0
            self._login_blocked_until = 0.0


class RemotePoolService:
    def __init__(
        self,
        accounts: AccountService,
        settings: SettingsService,
        events: EventLog,
    ):
        self.accounts = accounts
        self.settings = settings
        self.events = events
        self.on_waiting_changed: Callable[[str, bool], None] | None = None
        self._auth = RemoteAdminSession()

    def _config(self) -> dict[str, Any]:
        cfg = self.settings.registration_config()
        if not cfg.get("remote_base_url") or not cfg.get("remote_secret"):
            raise ValueError("请先配置远端 Base URL 和密码 / App Key")
        return cfg

    @staticmethod
    def _acquire_remote_slot(limit: int) -> None:
        global _REMOTE_ACTIVE
        with _REMOTE_SLOTS:
            while _REMOTE_ACTIVE >= limit:
                _REMOTE_SLOTS.wait()
            _REMOTE_ACTIVE += 1

    @staticmethod
    def _release_remote_slot() -> None:
        global _REMOTE_ACTIVE
        with _REMOTE_SLOTS:
            _REMOTE_ACTIVE = max(0, _REMOTE_ACTIVE - 1)
            _REMOTE_SLOTS.notify_all()

    def wait_for_cooldown(self, stream_id: str = "remote") -> None:
        with _REMOTE_COOLDOWN:
            remaining = _REMOTE_COOLDOWN_UNTIL - time.monotonic()
        if remaining <= 0:
            return
        if self.on_waiting_changed:
            self.on_waiting_changed(stream_id, True)
        self.events.publish(
            stream_id,
            f"[!] 远端入池触发全局限流，等待 {max(1, round(remaining))} 秒后继续",
            "warning",
        )
        with _REMOTE_COOLDOWN:
            while True:
                remaining = _REMOTE_COOLDOWN_UNTIL - time.monotonic()
                if remaining <= 0:
                    if self.on_waiting_changed:
                        self.on_waiting_changed(stream_id, False)
                    return
                _REMOTE_COOLDOWN.wait(timeout=remaining)

    def retry_waiting(self, stream_id: str) -> bool:
        global _REMOTE_COOLDOWN_UNTIL
        with _REMOTE_COOLDOWN:
            was_waiting = _REMOTE_COOLDOWN_UNTIL > time.monotonic()
            _REMOTE_COOLDOWN_UNTIL = time.monotonic()
            _REMOTE_COOLDOWN.notify_all()
        self.events.publish(
            stream_id,
            "[*] 已手动解除远端限流等待，所有远端任务立即重试",
            "warning",
        )
        return was_waiting

    def _activate_cooldown(self, stream_id: str, seconds: int) -> None:
        global _REMOTE_COOLDOWN_UNTIL
        with _REMOTE_COOLDOWN:
            _REMOTE_COOLDOWN_UNTIL = max(
                _REMOTE_COOLDOWN_UNTIL,
                time.monotonic() + seconds,
            )
            _REMOTE_COOLDOWN.notify_all()
        if self.on_waiting_changed:
            self.on_waiting_changed(stream_id, True)
        self.events.publish(
            stream_id,
            f"[!] 远端返回 429，所有远端入池操作统一等待 {seconds} 秒",
            "warning",
        )

    @staticmethod
    def _upload(
        client: httpx.Client,
        endpoint: str,
        access: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {access}", "Accept": "text/event-stream"},
            files={"files": (filename, content, "application/json")},
        )
        response.raise_for_status()
        return _parse_sse(response)

    def push(self, account_id: str, mode: str, stream_id: str = "remote") -> dict[str, Any]:
        account = self.accounts.get(account_id, reveal=True)
        if not account:
            raise ValueError("账号不存在")
        cfg = self._config()
        selected_mode = _MODE_ALIASES.get(str(mode).lower(), str(mode).lower())
        if selected_mode not in _IMPORT_PATHS:
            raise ValueError("不支持的远端入池类型")
        status_field = _STATUS_FIELDS[selected_mode]
        mode_label = _MODE_LABELS[selected_mode]
        concurrency = max(1, min(int(cfg.get("remote_operation_concurrency", 4) or 4), 50))
        self.wait_for_cooldown(stream_id)
        self._acquire_remote_slot(concurrency)
        try:
            # 排队期间其他请求可能触发 429，获得槽位后必须再次检查全局冷却。
            self.wait_for_cooldown(stream_id)
            self.accounts.set_status(account_id, status_field, "running")
            self.events.publish(stream_id, f"[*] 开始{mode_label}：{account['email']}")
            access = ""
            try:
                origin, access = self._auth.access_token(cfg)
                endpoint = f"{origin}{_IMPORT_PATHS[selected_mode]}"
                filename, content = self._import_file(selected_mode, account)
                with httpx.Client(timeout=120, follow_redirects=True) as client:
                    try:
                        result = self._upload(client, endpoint, access, filename, content)
                    except httpx.HTTPStatusError as upload_exc:
                        if upload_exc.response.status_code != 401:
                            raise
                        origin, access = self._auth.access_token(
                            cfg,
                            force_refresh=True,
                            stale_token=access,
                        )
                        endpoint = f"{origin}{_IMPORT_PATHS[selected_mode]}"
                        result = self._upload(client, endpoint, access, filename, content)
                self.accounts.set_status(account_id, status_field, "success")
                self.events.publish(stream_id, f"[+] {mode_label}成功：{account['email']}", "success")
                return {"mode": selected_mode, "endpoint": endpoint, "result": result}
            except Exception as exc:
                if _is_rate_limited(exc):
                    cooldown_seconds = _rate_limit_cooldown(exc)
                    self._activate_cooldown(stream_id, cooldown_seconds)
                    self.accounts.set_status(
                        account_id,
                        status_field,
                        "waiting",
                        f"远端限流，等待 {cooldown_seconds} 秒后重试",
                    )
                    raise RemoteRateLimitedError(
                        f"远端限流，等待 {cooldown_seconds} 秒后重试"
                    ) from exc
                safe_error = redact_error(
                    exc,
                    (cfg.get("remote_secret"), account.get("sso"), access),
                )
                self.accounts.set_status(account_id, status_field, "failed", safe_error)
                self.events.publish(stream_id, f"[-] {mode_label}失败：{account['email']}：{safe_error}", "error")
                raise RuntimeError(safe_error) from exc
        finally:
            self._release_remote_slot()

    def _import_file(self, mode: str, account: dict[str, Any]) -> tuple[str, bytes]:
        if mode == "build":
            cpa_file = Path(str(account.get("cpa_file") or "")).resolve()
            if (
                account.get("oidc_status") != "success"
                or not cpa_file.is_file()
                or cpa_file.parent != CPA_AUTH_DIR.resolve()
            ):
                raise RuntimeError("auths 尚未生成，请先生成 auths")
            return cpa_file.name, cpa_file.read_bytes()

        token = _normalize_sso(str(account.get("sso") or ""))
        if not token:
            raise RuntimeError("账号没有可用的 SSO Token")
        if len(token.encode("utf-8")) > 16 * 1024:
            raise RuntimeError("SSO Token 超过远端接口允许的 16 KiB")
        provider = "grok_web" if mode == "web" else "grok_console"
        item = {
            "name": account["email"],
            "email": account["email"],
            "sso_token": token,
        }
        if mode == "web":
            item["tier"] = "auto"
        payload = {"provider": provider, "accounts": [item]}
        filename = f"{mode}-{account['id']}.json"
        return filename, json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def test_connection(self) -> dict[str, Any]:
        cfg = self._config()
        try:
            origin, _ = self._auth.access_token(cfg, force_refresh=True)
            return {"ok": True, "mode": "admin", "endpoint": f"{origin}/api/admin/v1/auth/refresh"}
        except Exception as exc:
            safe_error = redact_error(exc, (cfg.get("remote_secret"),))
            return {"ok": False, "mode": "admin", "error": safe_error}

    def close(self) -> None:
        self._auth.close()
