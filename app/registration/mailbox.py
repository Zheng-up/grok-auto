from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from app.registration.models import RegistrationCancelled
from grok2api.upstream.moemail import create_mailbox, fetch_messages, normalize_mail_provider, pick_domain_from_list


@dataclass(frozen=True)
class Mailbox:
    provider: str
    email: str
    mailbox_id: str
    token: str
    api_key: str
    base_url: str

    def wait_for_code(
        self,
        *,
        timeout: float = 120,
        cancelled: Callable[[], bool] | None = None,
        tick: Callable[[int], None] | None = None,
    ) -> str:
        deadline = time.time() + timeout
        last_tick = -1
        while time.time() < deadline:
            if cancelled and cancelled():
                raise RegistrationCancelled("cancelled while waiting for email code")
            try:
                messages = fetch_messages(
                    self.mailbox_id,
                    provider=self.provider,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    include_details=True,
                    address=self.email,
                    token=self.token,
                )
                for item in messages:
                    text = "\n".join(
                        str(item.get(key) or "")
                        for key in (
                            "subject", "content", "text", "textBody", "html",
                            "htmlBody", "body", "from", "from_address", "verificationCode",
                        )
                    )
                    match = re.search(r"\b([A-Z0-9]{3})-([A-Z0-9]{3})\b", text, re.I)
                    if match:
                        return "".join(match.groups()).upper()
                    extracted = item.get("extracted") if isinstance(item, dict) else None
                    for code in (extracted or {}).get("codes", []):
                        clean = re.sub(r"[^A-Z0-9]", "", str(code).upper())
                        if len(clean) == 6:
                            return clean
                    match = re.search(r"\b([A-Z0-9]{6})\b", text, re.I)
                    if match and "x.ai" in text.lower():
                        return match.group(1).upper()
            except Exception:
                pass
            elapsed = int(timeout - max(0, deadline - time.time()))
            if tick and elapsed != last_tick and elapsed % 2 == 0:
                last_tick = elapsed
                tick(elapsed)
            time.sleep(1)
        raise RuntimeError("timeout waiting for xAI email verification code")


def create_registration_mailbox(
    *,
    provider: str,
    api_key: str,
    base_url: str,
    domains: str,
    slot: int,
    proxy: str = "",
) -> Mailbox:
    normalized = normalize_mail_provider(provider, base_url=base_url)
    if normalized != "tempmail" and not api_key:
        raise ValueError(f"mail API key missing for provider={normalized}")
    domain = pick_domain_from_list(domains, index=slot, strategy="round_robin")
    created = create_mailbox(
        provider=normalized,
        name=secrets.token_hex(5),
        domain=domain or None,
        api_key=api_key or None,
        base_url=base_url or None,
        proxy=proxy or None,
    )
    email = str(created.get("email") or "").strip()
    mailbox_id = str(created.get("id") or email).strip()
    if not email or not mailbox_id:
        raise RuntimeError("mail provider returned incomplete mailbox")
    return Mailbox(
        provider=normalized,
        email=email,
        mailbox_id=mailbox_id,
        token=str(created.get("token") or ""),
        api_key=api_key,
        base_url=base_url,
    )