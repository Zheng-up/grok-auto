from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.cpa.schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from app.cpa.writer import write_cpa_xai_auth
from app.config import CPA_AUTH_DIR
from app.redaction import redact_error
from app.services.accounts import AccountService
from app.services.events import EventLog
from app.services.settings import SettingsService
from scripts.sso_to_auth_json import sso_to_token


def _oidc_progress_label(message: str) -> str:
    if message.startswith("OIDC device flow "):
        return f"正在请求 Grok Build 授权 · {message.removeprefix('OIDC device flow ')}"
    if "invalid" in message.lower():
        return "SSO 会话无效"
    if "failed" in message.lower():
        return "Grok Build 授权失败"
    return message


def _mint_with_proxy(
    sso: str,
    proxy: str,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    return sso_to_token(sso, quiet=True, progress=progress, proxy=proxy)


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
        self.accounts.set_status(account_id, "oidc_status", "running")
        self.events.publish(stream_id, f"[*] 开始生成 auths：{account['email']}")
        proxy_pool = str(self.settings.get("proxy_pool", "") or "").strip()
        proxy = next((line.strip() for line in proxy_pool.splitlines() if line.strip()), "")
        try:
            token = _mint_with_proxy(
                account["sso"],
                proxy,
                progress=lambda message: self.events.publish(
                    stream_id,
                    f"[*] {account['email']} · {_oidc_progress_label(message)}",
                ),
            )
            if not token or not token.get("access_token") or not token.get("refresh_token"):
                raise RuntimeError("SSO device flow did not return renewable OAuth tokens")
            payload = build_cpa_xai_auth(
                email=account["email"],
                access_token=str(token["access_token"]),
                refresh_token=str(token["refresh_token"]),
                id_token=str(token.get("id_token") or "") or None,
                expires_in=int(token.get("expires_in") or 21600),
                base_url=DEFAULT_BASE_URL,
            )
            path = write_cpa_xai_auth(CPA_AUTH_DIR, payload)
            self.accounts.set_oidc(account_id, token, str(path))
            self.events.publish(stream_id, f"[+] auths 生成成功：{account['email']}", "success")
            if self.on_minted:
                try:
                    self.on_minted(account_id)
                except Exception as exc:
                    self.events.publish(stream_id, f"[!] auths 已生成，但自动 Build 入池排队失败：{exc}", "warning")
            return {"account_id": account_id, "email": account["email"], "path": str(path)}
        except Exception as exc:
            token_values = tuple((token or {}).values()) if "token" in locals() and isinstance(token, dict) else ()
            safe_error = redact_error(exc, (account.get("sso"), *token_values))
            self.accounts.set_status(account_id, "oidc_status", "failed", safe_error)
            self.events.publish(stream_id, f"[-] auths 生成失败：{account['email']}：{safe_error}", "error")
            raise RuntimeError(safe_error) from exc