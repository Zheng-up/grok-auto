from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AdminInitRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str
    password: str


class SettingsUpdateRequest(BaseModel):
    values: dict[str, Any]


class RegistrationStartRequest(BaseModel):
    count: int | None = Field(default=None, ge=1, le=25000)
    concurrency: int | None = Field(default=None, ge=1, le=50)
    overrides: dict[str, Any] = Field(default_factory=dict)


class AccountSelectionRequest(BaseModel):
    account_ids: list[str] = Field(default_factory=list)


class AccountOperationRequest(AccountSelectionRequest):
    kind: Literal["oidc", "remote_sso", "remote_web", "remote_cpa", "remote_console"]


class DeleteAccountsRequest(AccountSelectionRequest):
    pass