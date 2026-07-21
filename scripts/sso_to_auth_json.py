from __future__ import annotations

import contextvars
import os
import threading
import time
from typing import Any, Callable

from curl_cffi import requests

from grok2api.config import GROK_CLI_CLIENT_ID, OIDC_ISSUER, OIDC_SCOPES

_DEVICE_FLOW_LOCK = threading.RLock()
_DEVICE_FLOW_LAST_TS = 0.0
_REQUEST_PROXY: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "xai_request_proxy",
    default=None,
)


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(os.getenv(name, str(default)) or default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)) or default)))
    except (TypeError, ValueError):
        return default


def _http_timeout() -> float:
    return _env_float("GROK2API_SSO_HTTP_TIMEOUT", 12.0, 5.0, 60.0)


def _device_flow_retries() -> int:
    return _env_int("GROK2API_SSO_DEVICE_RETRIES", 8, 1, 16)


def _backoff(attempt: int) -> float:
    configured = os.getenv("GROK2API_SSO_DEVICE_BACKOFF_SEC", "").strip()
    if configured:
        try:
            return max(0.8, min(25.0, float(configured)))
        except ValueError:
            pass
    return max(0.8, min(25.0, 1.4 * (1.45 ** max(0, attempt - 1))))


def _wait_device_flow_slot() -> None:
    global _DEVICE_FLOW_LAST_TS
    gap = _env_float("GROK2API_SSO_DEVICE_GAP_SEC", 0.85, 0.0, 10.0)
    with _DEVICE_FLOW_LOCK:
        delay = (_DEVICE_FLOW_LAST_TS + gap) - time.time()
        if delay > 0:
            time.sleep(delay)
        _DEVICE_FLOW_LAST_TS = time.time()


def _proxy_kwargs() -> dict[str, Any]:
    request_proxy = _REQUEST_PROXY.get()
    value = (
        request_proxy
        if request_proxy is not None
        else (
            os.getenv("GROK2API_XAI_PROXY")
            or os.getenv("GROK2API_PROXY")
            or os.getenv("GROK_CLI_PROXY")
            or ""
        )
    ).strip()
    if "\n" in value or "\r" in value:
        value = next(
            (
                line.strip()
                for line in value.replace("\r", "\n").split("\n")
                if line.strip() and not line.strip().startswith("#")
            ),
            "",
        )
    return {"proxies": {"http": value, "https": value}} if value else {}


def _rate_limited(response: Any = None, error: Exception | None = None) -> bool:
    blob = " ".join(
        (
            str(getattr(response, "status_code", "") or ""),
            str(getattr(response, "url", "") or ""),
            str(getattr(response, "text", "") or "")[:500],
            str(error or ""),
        )
    ).lower()
    return any(marker in blob for marker in ("429", "slow_down", "rate_limited", "rate limit", "too many"))


def _request_device_code(session: Any) -> dict[str, Any] | None:
    retries = _device_flow_retries()
    for attempt in range(1, retries + 1):
        _wait_device_flow_slot()
        try:
            response = session.post(
                f"{OIDC_ISSUER}/oauth2/device/code",
                data={"client_id": GROK_CLI_CLIENT_ID, "scope": OIDC_SCOPES},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=_http_timeout(),
                **_proxy_kwargs(),
            )
            if response.status_code < 400:
                payload = response.json()
                return payload if isinstance(payload, dict) else None
            if not _rate_limited(response) or attempt >= retries:
                return None
        except Exception as exc:
            if not _rate_limited(error=exc) or attempt >= retries:
                return None
        time.sleep(_backoff(attempt))
    return None


def _poll_token(session: Any, device: dict[str, Any]) -> dict[str, Any] | None:
    interval = _env_float(
        "GROK2API_SSO_POLL_INTERVAL",
        min(float(device.get("interval") or 1), 1.5),
        0.2,
        10.0,
    )
    timeout = _env_float("GROK2API_SSO_POLL_TIMEOUT", 45.0, 5.0, 300.0)
    expires_in = max(1.0, float(device.get("expires_in") or 1800))
    deadline = time.time() + min(timeout, expires_in)
    first = True
    while time.time() < deadline:
        if not first:
            time.sleep(interval)
        first = False
        try:
            response = session.post(
                f"{OIDC_ISSUER}/oauth2/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": GROK_CLI_CLIENT_ID,
                    "device_code": str(device["device_code"]),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=_http_timeout(),
                **_proxy_kwargs(),
            )
            if response.status_code < 400:
                payload = response.json()
                return payload if isinstance(payload, dict) else None
            try:
                error = str((response.json() or {}).get("error") or "")
            except Exception:
                error = ""
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = min(10.0, interval + 1.0)
                continue
            return None
        except Exception:
            continue
    return None


def _approve_device(session: Any, device: dict[str, Any]) -> tuple[bool, bool]:
    try:
        session.get(
            str(device["verification_uri_complete"]),
            impersonate="chrome",
            timeout=_http_timeout(),
            **_proxy_kwargs(),
        )
        verified = session.post(
            f"{OIDC_ISSUER}/oauth2/device/verify",
            data={"user_code": str(device["user_code"])},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome",
            timeout=_http_timeout(),
            allow_redirects=True,
            **_proxy_kwargs(),
        )
        if "consent" not in str(verified.url or ""):
            return False, _rate_limited(verified)
        approved = session.post(
            f"{OIDC_ISSUER}/oauth2/device/approve",
            data={
                "user_code": str(device["user_code"]),
                "action": "allow",
                "principal_type": "User",
                "principal_id": "",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate="chrome",
            timeout=_http_timeout(),
            allow_redirects=True,
            **_proxy_kwargs(),
        )
        return "done" in str(approved.url or ""), _rate_limited(approved)
    except Exception as exc:
        return False, _rate_limited(error=exc)


def sso_to_token(
    sso_cookie: str,
    *,
    quiet: bool = False,
    progress: Callable[[str], None] | None = None,
    proxy: str | None = None,
) -> dict[str, Any] | None:
    """将一个 xAI SSO Cookie 转换为可续期的 OIDC token，不写入任何文件。"""
    token = str(sso_cookie or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    if not token:
        return None
    report = progress or ((lambda _message: None) if quiet else print)
    proxy_context = _REQUEST_PROXY.set(proxy) if proxy is not None else None
    session = requests.Session()
    session.cookies.set("sso", token, domain=".x.ai")
    try:
        home = session.get(
            "https://accounts.x.ai/",
            impersonate="chrome",
            timeout=_http_timeout(),
            **_proxy_kwargs(),
        )
        if "sign-in" in str(home.url or "") or "sign-up" in str(home.url or ""):
            report("SSO session is invalid")
            return None
        retries = _device_flow_retries()
        for attempt in range(1, retries + 1):
            report(f"OIDC device flow {attempt}/{retries}")
            device = _request_device_code(session)
            if device:
                approved, rate_limited = _approve_device(session, device)
                if approved:
                    result = _poll_token(session, device)
                    if result:
                        return result
                elif not rate_limited:
                    return None
            if attempt < retries:
                time.sleep(_backoff(attempt))
        return None
    except Exception as exc:
        report(f"OIDC conversion failed: {exc}")
        return None
    finally:
        session.close()
        if proxy_context is not None:
            _REQUEST_PROXY.reset(proxy_context)