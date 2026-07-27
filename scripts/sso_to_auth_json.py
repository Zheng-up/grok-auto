from __future__ import annotations

import base64
import contextvars
import json
import os
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

from curl_cffi import requests

from grok2api.config import GROK_CLI_CLIENT_ID, OIDC_ISSUER, OIDC_SCOPES

# Prefer a concrete Chrome JA3 preset. Bare "chrome" is unstable on some Windows
# curl_cffi builds and can raise OPENSSL_internal:invalid library (curl 35).
_IMPERSONATE = os.getenv("GROK2API_CURL_IMPERSONATE", "chrome131").strip() or "chrome131"

# Browser/account pages live on accounts.x.ai; OIDC machine APIs stay on auth.x.ai.
_DEVICE_PAGE_BASE = os.getenv("GROK2API_DEVICE_PAGE_BASE", "https://accounts.x.ai").rstrip("/")
_DEVICE_API_BASE = os.getenv("GROK2API_DEVICE_API_BASE", OIDC_ISSUER or "https://auth.x.ai").rstrip("/")

# Align with CLIProxyAPI internal/auth/xai/types.go Scope (CPA / Grok Build).
# Extra conversation scopes historically correlated with invalid_grant · Access denied.
_SCOPE_CPA = (
    os.getenv("GROK2API_OIDC_SCOPES", OIDC_SCOPES)
    or "openid profile email offline_access grok-cli:access api:access"
).strip()
_SCOPE_FULL = os.getenv(
    "GROK2API_OIDC_SCOPES_FULL",
    "openid profile email offline_access grok-cli:access api:access conversations:read conversations:write",
).strip()

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
    # cpa + full is enough; Access denied is permanent and must not spin 8 rounds.
    return _env_int("GROK2API_SSO_DEVICE_RETRIES", 2, 1, 16)


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
    lower = value.lower()
    # curl_cffi + socks5:// (local DNS) often hits OPENSSL_internal:invalid library.
    if lower.startswith("socks5://"):
        value = "socks5h://" + value.split("://", 1)[1]
    elif lower.startswith("socks4://") or lower.startswith("socks4a://"):
        value = "socks4h://" + value.split("://", 1)[1]
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


def _response_hint(response: Any = None, error: Exception | None = None) -> str:
    if error is not None:
        return str(error)[:180]
    if response is None:
        return "no response"
    status = getattr(response, "status_code", "?")
    url = str(getattr(response, "url", "") or "")
    content_type = str((getattr(response, "headers", {}) or {}).get("content-type") or "")
    body = str(getattr(response, "text", "") or "")
    err = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            err = str(payload.get("error") or payload.get("message") or "")[:80]
    except Exception:
        snippet = body[:80].replace("\n", " ").strip()
        if snippet.startswith("<!DOCTYPE") or snippet.startswith("<html"):
            err = "non-json html body"
        elif snippet:
            err = f"non-json body={snippet}"
        else:
            err = "empty body"
    parts = [f"status={status}"]
    if content_type:
        parts.append(f"content-type={content_type.split(';')[0]}")
    if url:
        parts.append(f"url={url}")
    if err:
        parts.append(f"detail={err}")
    return " · ".join(parts)


def _parse_json_dict(response: Any) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _is_tls_error(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    return any(
        marker in text
        for marker in (
            "ssl",
            "tls",
            "curl: (35)",
            "unexpected_eof",
            "openssl",
            "invalid library",
            "eof occurred in violation of protocol",
            "connection reset",
        )
    )


def _url_path(url: str) -> str:
    text = str(url or "")
    if "://" in text:
        text = text.split("://", 1)[1]
    return text.split("?", 1)[0].lower()


def _looks_like_consent(url: str) -> bool:
    path = _url_path(url)
    return "consent" in path


def _looks_like_done(url: str) -> bool:
    path = _url_path(url)
    return "device/done" in path or path.endswith("/oauth2/device/done")


def _b64url_json(segment: str) -> dict[str, Any]:
    raw = str(segment or "").strip()
    if not raw:
        return {}
    padded = raw + ("=" * ((4 - len(raw) % 4) % 4))
    try:
        data = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(data.decode("utf-8", errors="ignore") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _sso_claims(sso_cookie: str) -> dict[str, Any]:
    token = str(sso_cookie or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    return _b64url_json(parts[1])


def _principal_id_from_sso(sso_cookie: str) -> str:
    payload = _sso_claims(sso_cookie)
    for key in ("principal_id", "principalId", "sub", "user_id", "userId", "uid", "id"):
        value = str(payload.get(key) or "").strip()
        if value and value.lower() not in {"session", "session_id"}:
            return value
    return ""


def _principal_id_from_html(html: str) -> str:
    text = str(html or "")
    patterns = (
        r'"principalId"\s*:\s*"([^"]+)"',
        r'"principal_id"\s*:\s*"([^"]+)"',
        r'"userId"\s*:\s*"([^"]+)"',
        r'"user_id"\s*:\s*"([^"]+)"',
        r'principalId\\?":\\?"([0-9a-fA-F-]{8,})',
        r'name=["\']principal_id["\'][^>]*value=["\']([^"\']+)["\']',
        r'name=["\']principalId["\'][^>]*value=["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        value = str(match.group(1) or "").strip()
        if value:
            return value
    return ""


def _set_sso_cookies(session: Any, sso_cookie: str) -> None:
    token = str(sso_cookie or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    if not token:
        return
    # Keep session cookies on both accounts/auth hosts used by device flow.
    for name in ("sso", "sso-rw"):
        for domain in (".x.ai", "accounts.x.ai", "auth.x.ai", ".accounts.x.ai", ".auth.x.ai"):
            try:
                session.cookies.set(name, token, domain=domain, path="/")
            except Exception:
                try:
                    session.cookies.set(name, token, domain=domain)
                except Exception:
                    pass


def _extract_hidden_inputs(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    text = str(html or "")
    for match in re.finditer(r"<input\b[^>]*>", text, flags=re.I):
        tag = match.group(0)
        type_match = re.search(r"""\btype\s*=\s*["']?([^"'> \s]+)["']?""", tag, flags=re.I)
        input_type = (type_match.group(1) if type_match else "text").lower()
        if input_type != "hidden":
            continue
        name_match = re.search(r"""\bname\s*=\s*["']?([^"'> \s]+)["']?""", tag, flags=re.I)
        if not name_match:
            continue
        name = name_match.group(1).strip()
        if not name:
            continue
        value_match = re.search(r"""\bvalue\s*=\s*["']([^"']*)["']""", tag, flags=re.I)
        if not value_match:
            value_match = re.search(r"""\bvalue\s*=\s*([^"'> \s]+)""", tag, flags=re.I)
        fields[name] = value_match.group(1) if value_match else ""
    return fields


def _build_approve_form(
    *,
    user_code: str,
    principal_id: str,
    consent_html: str,
) -> dict[str, str]:
    form = _extract_hidden_inputs(consent_html)
    form["user_code"] = user_code
    form["action"] = "allow"
    form.setdefault("principal_type", "User")
    html_principal = _principal_id_from_html(consent_html)
    resolved = str(form.get("principal_id") or form.get("principalId") or "").strip()
    if not resolved:
        resolved = html_principal or principal_id
    form["principal_id"] = resolved
    form.pop("principalId", None)
    return {key: str(value) for key, value in form.items() if value is not None}


def _extract_form_action(html: str, fallback: str) -> str:
    text = str(html or "")

    def _normalize_action(raw: str) -> str:
        action = str(raw or "").strip()
        if not action:
            return ""
        if action.startswith("http"):
            return action
        if action.startswith("/"):
            return f"{_DEVICE_PAGE_BASE}{action}"
        return ""

    # Prefer forms that look like OAuth consent/device approve.
    form_blocks = re.findall(r"<form\b[^>]*>.*?</form>", text, flags=re.I | re.S)
    for block in form_blocks:
        lower = block.lower()
        if "cookie" in lower or "隐私" in block or "全部允许" in block:
            continue
        if any(marker in lower for marker in ("user_code", "principal", "allow", "grok", "action", "approve", "device")):
            action_match = re.search(
                r"""\baction\s*=\s*["']([^"']+)["']""",
                block,
                flags=re.I,
            ) or re.search(
                r"""\baction\s*=\s*([^\s>]+)""",
                block,
                flags=re.I,
            )
            if action_match:
                normalized = _normalize_action(action_match.group(1))
                if normalized:
                    return normalized
    action_match = re.search(
        r"""<form\b[^>]*\baction\s*=\s*["']([^"']+)["']""",
        text,
        flags=re.I,
    ) or re.search(
        r"""<form\b[^>]*\baction\s*=\s*([^\s>]+)""",
        text,
        flags=re.I,
    )
    if action_match:
        normalized = _normalize_action(action_match.group(1))
        if normalized:
            return normalized
    return fallback


def _extract_next_action(html: str) -> str:
    text = str(html or "")
    patterns = (
        r'next-action["\']?\s*[:=]\s*["\']([a-f0-9]{20,})["\']',
        r'next-action=([a-f0-9]{20,})',
        r'"nextAction"\s*:\s*"([a-f0-9]{20,})"',
        r'createServerReference\)?\([\'"]([a-f0-9]{20,})[\'"]',
        r'name=["\']next-action["\'][^>]*value=["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            value = str(match.group(1) or "").strip()
            if value:
                return value
    return ""


def _approve_targets(consent_url: str, consent_html: str) -> list[str]:
    targets: list[str] = []
    form_action = _extract_form_action(consent_html, "")
    if form_action:
        targets.append(form_action)
    if consent_url:
        targets.append(consent_url)
    targets.extend(
        (
            f"{_DEVICE_PAGE_BASE}/oauth2/device/approve",
            f"{_DEVICE_API_BASE}/oauth2/device/approve",
        )
    )
    # Preserve order, drop empties/duplicates.
    ordered: list[str] = []
    for item in targets:
        value = str(item or "").strip()
        if value and value not in ordered:
            ordered.append(value)
    return ordered


def _request_device_code(
    session: Any,
    report: Callable[[str], None],
    *,
    scope: str,
) -> dict[str, Any] | None:
    endpoint = f"{_DEVICE_API_BASE}/oauth2/device/code"
    retries = _device_flow_retries()
    for attempt in range(1, retries + 1):
        _wait_device_flow_slot()
        try:
            response = session.post(
                endpoint,
                data={"client_id": GROK_CLI_CLIENT_ID, "scope": scope},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate=_IMPERSONATE,
                timeout=_http_timeout(),
                **_proxy_kwargs(),
            )
            payload = _parse_json_dict(response)
            if response.status_code < 400 and payload:
                if payload.get("device_code") and payload.get("user_code"):
                    report(
                        f"[2/6] 申请 device code 成功 · POST {endpoint} · "
                        f"user_code={payload.get('user_code')}"
                    )
                    return payload
                report(f"[2/6] 申请 device code 响应缺字段 · POST {endpoint} · {_response_hint(response)}")
                return None
            report(f"[2/6] 申请 device code 失败 · POST {endpoint} · {_response_hint(response)}")
            if not _rate_limited(response) or attempt >= retries:
                return None
        except Exception as exc:
            report(f"[2/6] 申请 device code 异常 · POST {endpoint} · {_response_hint(error=exc)}")
            # TLS blips on Windows curl_cffi are common; keep trying within this round.
            if _is_tls_error(exc):
                time.sleep(min(1.2 * attempt, 3.0))
                continue
            if not _rate_limited(error=exc) or attempt >= retries:
                return None
        time.sleep(_backoff(attempt))
    return None


class DeviceFlowFatalError(RuntimeError):
    """Permanent device-flow rejection; do not start another full round."""


def _poll_token(
    session: Any,
    device: dict[str, Any],
    report: Callable[[str], None],
) -> dict[str, Any] | None:
    endpoint = f"{_DEVICE_API_BASE}/oauth2/token"
    interval = _env_float(
        "GROK2API_SSO_POLL_INTERVAL",
        min(float(device.get("interval") or 1), 1.5),
        0.2,
        10.0,
    )
    timeout = _env_float("GROK2API_SSO_POLL_TIMEOUT", 45.0, 5.0, 300.0)
    expires_in = max(1.0, float(device.get("expires_in") or 1800))
    deadline = time.time() + min(timeout, expires_in)
    # Some IdPs briefly return invalid_grant right after approve before the grant is visible.
    invalid_grant_retry_until = time.time() + _env_float(
        "GROK2API_SSO_INVALID_GRANT_GRACE_SEC",
        8.0,
        0.0,
        30.0,
    )
    first = True
    last_error = ""
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": GROK_CLI_CLIENT_ID,
        "device_code": str(device["device_code"]),
    }
    while time.time() < deadline:
        if not first:
            time.sleep(interval)
        first = False
        try:
            response = session.post(
                endpoint,
                data=form,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "Origin": _DEVICE_API_BASE,
                    "Referer": f"{_DEVICE_PAGE_BASE}/",
                },
                impersonate=_IMPERSONATE,
                timeout=_http_timeout(),
                **_proxy_kwargs(),
            )
            payload = _parse_json_dict(response)
            if response.status_code < 400 and payload:
                if payload.get("access_token"):
                    report(f"[6/6] 换 token 成功 · POST {endpoint}")
                    return payload
                report(f"[6/6] 换 token 响应缺 access_token · POST {endpoint} · {_response_hint(response)}")
                return None
            error = str((payload or {}).get("error") or "") if payload else ""
            desc = str((payload or {}).get("error_description") or "") if payload else ""
            last_error = " · ".join(part for part in (error, desc) if part) or _response_hint(response)
            if error == "authorization_pending":
                report(f"[6/6] 换 token 等待授权完成 · authorization_pending")
                continue
            if error == "slow_down":
                interval = min(10.0, interval + 1.0)
                report(f"[6/6] 换 token 限速 · slow_down")
                continue
            # Permanent: Access denied — fake approve / low-trust session.
            if error in {"access_denied", "invalid_grant"} and "access denied" in desc.lower():
                report(f"[6/6] 换 token 永久拒绝 · {last_error}")
                raise DeviceFlowFatalError(last_error)
            if error == "invalid_grant" and time.time() < invalid_grant_retry_until:
                report(f"[6/6] 换 token 暂态 invalid_grant，短暂重试 · {last_error}")
                interval = max(interval, 0.8)
                continue
            if error in {"access_denied", "expired_token"}:
                report(f"[6/6] 换 token 永久失败 · {last_error}")
                raise DeviceFlowFatalError(last_error)
            report(f"[6/6] 换 token 失败 · {last_error}")
            return None
        except DeviceFlowFatalError:
            raise
        except Exception as exc:
            last_error = _response_hint(error=exc)
            continue
    report(f"[6/6] 换 token 超时 · last={last_error or 'none'}")
    return None


def _device_page_url(device: dict[str, Any]) -> str:
    """Prefer upstream verification_uri_complete; fall back to accounts device page."""
    complete = str(device.get("verification_uri_complete") or "").strip()
    if complete:
        return complete
    user_code = str(device.get("user_code") or "").strip()
    if not user_code:
        raise ValueError("device flow response missing user_code")
    # Keep letters/digits/hyphen unescaped; encode any unexpected characters.
    return f"{_DEVICE_PAGE_BASE}/oauth2/device?user_code={quote(user_code, safe='-')}"


def _approve_device(
    session: Any,
    device: dict[str, Any],
    report: Callable[[str], None],
    *,
    principal_id: str = "",
) -> tuple[bool, bool]:
    page_url = _device_page_url(device)
    # verify remains on auth.x.ai; approve prefers consent-page form action / consent URL
    # because the modern accounts UI is Next.js and may not honor bare /device/approve.
    verify_url = f"{_DEVICE_API_BASE}/oauth2/device/verify"
    user_code = str(device.get("user_code") or "").strip()
    try:
        report(f"[3/6] 打开验证页")
        page = session.get(
            page_url,
            impersonate=_IMPERSONATE,
            timeout=_http_timeout(),
            **_proxy_kwargs(),
        )
        report(f"[3/6] 打开验证页完成 · status={getattr(page, 'status_code', '?')}")

        report("[4/6] 校验 user_code")
        verified = session.post(
            verify_url,
            data={"user_code": user_code},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            impersonate=_IMPERSONATE,
            timeout=_http_timeout(),
            allow_redirects=True,
            **_proxy_kwargs(),
        )
        verified_url = str(verified.url or "")
        consent_html = str(getattr(verified, "text", "") or "")
        report(f"[4/6] 校验完成 · consent={'yes' if _looks_like_consent(verified_url) else 'no'}")
        # Only trust the final URL path. Account HTML pages may contain the word
        # "consent" and previously caused false positives.
        if not _looks_like_consent(verified_url):
            report(f"[4/6] 校验 user_code 未进入 consent · final_url={verified_url or 'empty'}")
            return False, _rate_limited(verified)

        html_principal = _principal_id_from_html(consent_html)
        resolved_principal = html_principal or principal_id
        approve_form = _build_approve_form(
            user_code=user_code,
            principal_id=resolved_principal,
            consent_html=consent_html,
        )
        next_action = _extract_next_action(consent_html)
        targets = _approve_targets(verified_url, consent_html)
        hidden_keys = sorted(
            key
            for key in approve_form.keys()
            if key not in {"user_code", "action", "principal_type", "principal_id"}
        )
        last_rate_limited = False
        for index, approve_url in enumerate(targets, start=1):
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": _DEVICE_PAGE_BASE,
                "Referer": verified_url or page_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            if next_action:
                headers["next-action"] = next_action
            report(
                f"[5/6] 批准 {index}/{len(targets)} · "
                f"principal={'yes' if approve_form.get('principal_id') else 'no'} · "
                f"hidden={len(hidden_keys)}"
            )
            approved = session.post(
                approve_url,
                data=approve_form,
                headers=headers,
                impersonate=_IMPERSONATE,
                timeout=_http_timeout(),
                allow_redirects=True,
                **_proxy_kwargs(),
            )
            approved_url = str(approved.url or "")
            report(f"[5/6] 批准完成 · done={'yes' if _looks_like_done(approved_url) else 'no'}")
            if _looks_like_done(approved_url):
                return True, _rate_limited(approved)
            last_rate_limited = _rate_limited(approved)
            report(f"[5/6] 未到 done · url={approved_url or 'empty'}")
        return False, last_rate_limited
    except Exception as exc:
        report(f"[3-5/6] 验证/批准异常 · {_response_hint(error=exc)}")
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
    _set_sso_cookies(session, token)
    try:
        principal_id = _principal_id_from_sso(token)
        claims = _sso_claims(token)
        claim_keys = ",".join(sorted(str(key) for key in claims.keys())[:8]) or "none"
        report(f"[1/6] 校验 SSO · GET {_DEVICE_PAGE_BASE}/")
        home = session.get(
            f"{_DEVICE_PAGE_BASE}/",
            impersonate=_IMPERSONATE,
            timeout=_http_timeout(),
            **_proxy_kwargs(),
        )
        home_url = str(home.url or "")
        home_html = str(getattr(home, "text", "") or "")
        if not principal_id:
            principal_id = _principal_id_from_html(home_html)
        report(
            f"[1/6] 校验 SSO 完成 · {_response_hint(home)} · "
            f"principal_id={'yes' if principal_id else 'no'} · "
            f"sso_claims={claim_keys}"
        )
        if "sign-in" in home_url or "sign-up" in home_url:
            report("[1/6] SSO 会话无效 · 跳转到登录/注册页")
            return None
        retries = _device_flow_retries()
        # 焚绝 / CPA：优先 CPA scope；仅在仍 Access denied 时再试 full 一次。
        scope_candidates = [_SCOPE_CPA]
        if _SCOPE_FULL and _SCOPE_FULL != _SCOPE_CPA:
            scope_candidates.append(_SCOPE_FULL)
        for attempt in range(1, retries + 1):
            scope = scope_candidates[(attempt - 1) % len(scope_candidates)]
            report(
                f"整轮授权 {attempt}/{retries} · "
                f"scope={'cpa' if scope == _SCOPE_CPA else 'full'}"
            )
            report(f"[2/6] 申请 device code")
            device = _request_device_code(session, report, scope=scope)
            if not device:
                report("[2/6] 本轮未拿到 device_code")
            else:
                complete = str(device.get("verification_uri_complete") or "")
                report(
                    f"[2/6] device 就绪 · user_code={device.get('user_code')} · "
                    f"has_complete={'yes' if complete else 'no'}"
                )
                approved, rate_limited = _approve_device(
                    session,
                    device,
                    report,
                    principal_id=principal_id,
                )
                if approved:
                    report("[6/6] 换 token")
                    try:
                        result = _poll_token(session, device, report)
                    except DeviceFlowFatalError as exc:
                        # Access denied after done is permanent for this SSO/session shape.
                        # Try the alternate scope once if available, then stop.
                        next_scope_left = attempt < len(scope_candidates) and attempt < retries
                        if next_scope_left and scope == _SCOPE_CPA and _SCOPE_FULL != _SCOPE_CPA:
                            report(f"[6/6] 永久拒绝，切换 scope 再试一轮 · {exc}")
                        else:
                            report(f"[6/6] 永久拒绝，停止重试 · {exc}")
                            return None
                    else:
                        if result:
                            report("Device Flow 完成 · 已拿到 OAuth token")
                            return result
                        report("[6/6] 本轮换 token 失败")
                elif rate_limited:
                    report("[3-5/6] 验证/批准限流 · 将重试")
                else:
                    report("[3-5/6] 验证/批准失败")
            if attempt < retries:
                time.sleep(_backoff(attempt))
        report(f"Device Flow 全部重试耗尽 · {retries}/{retries}")
        return None
    except Exception as exc:
        report(f"OIDC conversion failed: {exc}")
        return None
    finally:
        session.close()
        if proxy_context is not None:
            _REQUEST_PROXY.reset(proxy_context)