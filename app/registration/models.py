from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable


class RegistrationStage(StrEnum):
    QUEUED = "queued"
    MAILBOX = "mailbox"
    SIGNUP_PAGE = "signup_page"
    TURNSTILE = "turnstile"
    EMAIL_CODE = "email_code"
    CREATE_ACCOUNT = "create_account"
    SSO = "sso"
    OIDC = "oidc"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RegistrationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistrationRequest:
    mail_provider: str
    mail_api_key: str = ""
    mail_base_url: str = ""
    mail_domain: str = ""
    captcha_provider: str = "local"
    captcha_api_key: str = ""
    local_solver_url: str = "http://127.0.0.1:5072"
    proxy: str = ""
    mail_poll_timeout: int = 180
    retry_limit: int = 1


@dataclass(frozen=True)
class RegisteredAccount:
    email: str
    password: str
    sso: str
    oauth: dict[str, Any] | None = None


@dataclass
class RegistrationContext:
    progress: Callable[[RegistrationStage, str], None]
    cancelled: Callable[[], bool]
    extra: dict[str, Any] = field(default_factory=dict)

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise RegistrationCancelled("registration cancelled")

    def update(self, stage: RegistrationStage, message: str) -> None:
        self.check_cancelled()
        self.progress(stage, message)