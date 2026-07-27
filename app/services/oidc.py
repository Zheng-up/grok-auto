from __future__ import annotations

import threading
from typing import Any, Callable

from app.cpa.schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from app.cpa.writer import write_cpa_xai_auth
from app.config import CPA_AUTH_DIR, runtime
from app.redaction import redact_error
from app.services.accounts import AccountService
from app.services.events import EventLog
from app.services.settings import SettingsService
from app.vendor.grok_build_auth.xconsole_client.client import XConsoleAuthClient
from app.vendor.grok_build_auth.xconsole_client.solver import YesCaptchaSolver
from scripts.sso_to_auth_json import sso_to_token

# 焚绝协议授权：本地过盾 → CreateSession → SSO → device-flow → CPA auth
SIGNIN_URL = "https://accounts.x.ai/sign-in?redirect=grok-com"
_LOCAL_CAPTCHA_LIMIT = threading.BoundedSemaphore(runtime.local_solver_max_concurrency)


def _oidc_progress_label(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return text
    if (
        text.startswith("[")
        or text.startswith("整轮授权")
        or text.startswith("Device Flow")
    ):
        return text
    if text.startswith("OIDC device flow "):
        return f"整轮授权 · {text.removeprefix('OIDC device flow ')}"
    if "invalid" in text.lower() and "grant" not in text.lower():
        return "SSO 会话无效"
    if text.startswith("OIDC conversion failed:"):
        return f"Grok Build 授权失败 · {text.removeprefix('OIDC conversion failed:').strip()}"
    if "failed" in text.lower() and not text.startswith("failed:"):
        return f"Grok Build 授权失败 · {text}"
    return text


def _first_proxy(proxy_pool: str) -> str:
    return next((line.strip() for line in str(proxy_pool or "").splitlines() if line.strip()), "")


def _solve_signin_turnstile(
    client: XConsoleAuthClient,
    *,
    captcha_provider: str,
    captcha_api_key: str,
    local_solver_url: str,
    progress: Callable[[str], None],
) -> str:
    sitekey = str(getattr(client, "turnstile_sitekey", "") or "").strip()
    if not sitekey:
        from app.vendor.grok_build_auth.xconsole_client import config as protocol_config

        sitekey = str(getattr(protocol_config, "TURNSTILE_SITEKEY", "") or "").strip()
    if not sitekey:
        raise RuntimeError("Turnstile sitekey missing from sign-in page")

    local = captcha_provider == "local"
    endpoint = local_solver_url if local else None
    key = "local" if local else captcha_api_key
    if not key:
        raise RuntimeError("captcha API key is required for CreateSession")

    solver = YesCaptchaSolver(
        key,
        endpoint=endpoint,
        timeout=120,
        poll_interval=2,
        auto_fallback_endpoint=not local,
        on_progress=lambda message: (
            None if not str(message or "").strip() else progress(f"Turnstile 验证：{message}")
        ),
    )

    def solve() -> str:
        return solver.solve_turnstile(
            website_url=SIGNIN_URL,
            website_key=sitekey,
            premium=not local,
            fallback_non_premium=True,
        )

    if local:
        with _LOCAL_CAPTCHA_LIMIT:
            return solve()
    return solve()


def _create_session_sso(
    *,
    email: str,
    password: str,
    proxy: str,
    captcha_provider: str,
    captcha_api_key: str,
    local_solver_url: str,
    progress: Callable[[str], None],
) -> str:
    """焚绝第 1 步：本地过盾 + 密码 CreateSession → 新鲜 SSO。"""
    client: XConsoleAuthClient | None = None
    try:
        progress("打开登录页 · 准备 CreateSession")
        client = XConsoleAuthClient(
            debug=False,
            proxy=proxy or None,
            signup_url=SIGNIN_URL,
            impersonate="chrome131",
            timeout=20.0,
        )
        # Warm CF / page cookies; sign-in HTML also carries turnstile sitekey.
        try:
            client.visit_home()
        except Exception:
            pass
        try:
            status, _hdrs, _sc, raw = client._request(  # noqa: SLF001
                "GET",
                SIGNIN_URL,
                headers={
                    **client._base_headers(),
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "sec-fetch-site": "same-origin",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-dest": "document",
                },
            )
            html = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
            scraped = client._scrape_turnstile_sitekey(html)  # noqa: SLF001
            if scraped:
                client.turnstile_sitekey = scraped
            progress(f"登录页就绪 · status={status} · sitekey={'yes' if scraped else 'fallback'}")
        except Exception as exc:
            progress(f"登录页预热失败，继续 CreateSession · {exc}")

        progress("本地过盾 · 求解 Turnstile")
        turnstile = _solve_signin_turnstile(
            client,
            captcha_provider=captcha_provider,
            captcha_api_key=captcha_api_key,
            local_solver_url=local_solver_url,
            progress=progress,
        )
        progress("CreateSession · 密码登录换 SSO")
        sso = client.obtain_session_via_password(
            email=email,
            password=password,
            turnstile_token=turnstile,
            referer=SIGNIN_URL,
            retries=3,
        )
        if not sso:
            raise RuntimeError("CreateSession did not return SSO session")
        progress("CreateSession 完成 · 已拿到 SSO")
        return str(sso).strip()
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


class OidcService:
    def __init__(
        self,
        accounts: AccountService,
        settings: SettingsService,
        events: EventLog,
    ):
        self.accounts = accounts
        self.settings = settings
        self.events = events
        self.on_minted: Callable[[str], None] | None = None

    def mint(self, account_id: str, stream_id: str = "oidc") -> dict[str, Any]:
        account = self.accounts.get(account_id, reveal=True)
        if not account:
            raise ValueError("account not found")
        email = str(account.get("email") or "")
        password = str(account.get("password") or "")
        stored_sso = str(account.get("sso") or "").strip()
        self.accounts.set_status(account_id, "oidc_status", "running")
        self.events.publish(stream_id, f"[*] 开始生成 auths（焚绝协议流）：{email}")

        cfg = self.settings.registration_config()
        proxy = _first_proxy(str(cfg.get("proxy_pool") or ""))
        captcha_provider = str(cfg.get("captcha_provider") or "local").strip().lower() or "local"
        captcha_api_key = str(cfg.get("captcha_api_key") or "")
        local_solver_url = str(
            cfg.get("local_solver_url") or "http://127.0.0.1:5072"
        ).strip()

        def progress(message: str) -> None:
            self.events.publish(
                stream_id,
                f"[*] {email} · {_oidc_progress_label(message)}",
            )

        try:
            # --- 1/4 本地过盾 + CreateSession → SSO ---
            sso = ""
            if password:
                progress("[1/4] 本地过盾 + CreateSession → SSO")
                try:
                    sso = _create_session_sso(
                        email=email,
                        password=password,
                        proxy=proxy,
                        captcha_provider=captcha_provider,
                        captcha_api_key=captcha_api_key,
                        local_solver_url=local_solver_url,
                        progress=progress,
                    )
                    if sso and sso != stored_sso:
                        self.accounts.update_sso(account_id, sso)
                        progress("已用 CreateSession SSO 更新本地账号")
                except Exception as exc:
                    progress(f"CreateSession 失败，回退已存 SSO · {exc}")
            if not sso:
                sso = stored_sso
            if not sso:
                raise RuntimeError("missing SSO after CreateSession / stored session")

            # --- 2/4 Device Flow（CPA client_id + scope，纯协议）---
            progress("[2/4] Device Flow 授权 · 申请 user_code / 校验 / 允许 / 换 token")
            token = sso_to_token(sso, quiet=True, progress=progress, proxy=proxy)
            if not token or not token.get("access_token") or not token.get("refresh_token"):
                raise RuntimeError("SSO device flow did not return renewable OAuth tokens")

            # --- 3/4 写入 CPA Auth（CLIProxyAPI xai-*.json）---
            progress("[3/4] 写入 CPA Auth / SUB Auth")
            payload = build_cpa_xai_auth(
                email=email,
                access_token=str(token["access_token"]),
                refresh_token=str(token["refresh_token"]),
                id_token=str(token.get("id_token") or "") or None,
                expires_in=int(token.get("expires_in") or 21600),
                base_url=DEFAULT_BASE_URL,
            )
            path = write_cpa_xai_auth(CPA_AUTH_DIR, payload)
            self.accounts.set_oidc(account_id, token, str(path))

            # --- 4/4 完成 ---
            progress(f"[4/4] 完成 · {path.name}")
            self.events.publish(stream_id, f"[+] auths 生成成功：{email}", "success")
            if self.on_minted:
                try:
                    self.on_minted(account_id)
                except Exception as exc:
                    self.events.publish(
                        stream_id,
                        f"[!] auths 已生成，但自动 Build 入池排队失败：{exc}",
                        "warning",
                    )
            return {"account_id": account_id, "email": email, "path": str(path)}
        except Exception as exc:
            token_values = (
                tuple((token or {}).values())
                if "token" in locals() and isinstance(token, dict)
                else ()
            )
            safe_error = redact_error(exc, (account.get("sso"), sso if "sso" in locals() else None, *token_values))
            self.accounts.set_status(account_id, "oidc_status", "failed", safe_error)
            self.events.publish(stream_id, f"[-] auths 生成失败：{email}：{safe_error}", "error")
            raise RuntimeError(safe_error) from exc