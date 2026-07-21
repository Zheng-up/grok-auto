from __future__ import annotations

import os
import re
import threading
import time
import uuid
from typing import Any

from app.config import runtime
from app.registration.mailbox import Mailbox, create_registration_mailbox
from app.registration.models import (
    RegisteredAccount,
    RegistrationContext,
    RegistrationRequest,
    RegistrationStage,
)
from app.vendor.grok_build_auth.xconsole_client import XConsoleAuthClient, YesCaptchaSolver

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"
SIGNIN_URL = "https://accounts.x.ai/sign-in?redirect=grok-com"
_LOCAL_CAPTCHA_LIMIT = threading.BoundedSemaphore(runtime.local_solver_max_concurrency)


def _password() -> str:
    return f"Aa{os.urandom(7).hex()}9!xZ"


def _solve_turnstile(
    client: XConsoleAuthClient,
    request: RegistrationRequest,
    context: RegistrationContext,
    *,
    url: str = SIGNUP_URL,
) -> str:
    sitekey = str(getattr(client, "turnstile_sitekey", "") or "").strip()
    if not sitekey:
        from app.vendor.grok_build_auth.xconsole_client import config as protocol_config

        sitekey = str(getattr(protocol_config, "TURNSTILE_SITEKEY", "") or "").strip()
    if not sitekey:
        raise RuntimeError("Turnstile sitekey missing from live signup page")

    local = request.captcha_provider == "local"
    endpoint = request.local_solver_url if local else None
    key = "local" if local else request.captcha_api_key
    if not key:
        raise RuntimeError("captcha API key is required")

    solver = YesCaptchaSolver(
        key,
        endpoint=endpoint,
        timeout=120,
        poll_interval=2,
        auto_fallback_endpoint=not local,
        on_progress=lambda message: context.update(
            RegistrationStage.TURNSTILE, f"Turnstile 验证：{message}"
        ),
    )
    solve = lambda: solver.solve_turnstile(
        website_url=url,
        website_key=sitekey,
        premium=not local,
        fallback_non_premium=True,
    )
    if local:
        with _LOCAL_CAPTCHA_LIMIT:
            context.check_cancelled()
            return solve()
    return solve()


def _clean_code(value: str) -> str:
    code = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if len(code) != 6:
        raise RuntimeError(f"invalid verification code shape: {code!r}")
    return code


def _create_account(
    client: XConsoleAuthClient,
    mailbox: Mailbox,
    password: str,
    turnstile: str,
    request: RegistrationRequest,
    context: RegistrationContext,
) -> Any:
    context.update(RegistrationStage.EMAIL_CODE, "正在发送邮箱验证码")
    client.create_email_validation_code(mailbox.email)
    code = _clean_code(
        mailbox.wait_for_code(
            timeout=request.mail_poll_timeout,
            cancelled=context.cancelled,
            tick=lambda elapsed: context.update(
                RegistrationStage.EMAIL_CODE,
                f"正在等待邮箱验证码 · {elapsed} 秒",
            ),
        )
    )
    context.update(RegistrationStage.EMAIL_CODE, "邮箱验证码已获取")

    last_error = ""
    for attempt in range(1, 4):
        context.check_cancelled()
        if attempt > 1:
            context.update(
                RegistrationStage.TURNSTILE,
                f"正在刷新 Turnstile · 创建重试 {attempt}/3",
            )
            turnstile = _solve_turnstile(client, request, context)
            if "validation-code" in last_error or "expired" in last_error:
                client.create_email_validation_code(mailbox.email)
                code = _clean_code(
                    mailbox.wait_for_code(
                        timeout=request.mail_poll_timeout,
                        cancelled=context.cancelled,
                    )
                )
        client.verify_email_validation_code(mailbox.email, code)
        context.update(
            RegistrationStage.CREATE_ACCOUNT,
            f"正在创建 xAI 账号 · 第 {attempt}/3 次",
        )
        result = client.create_account(
            email=mailbox.email,
            given_name="User",
            family_name="Grok",
            password=password,
            email_validation_code=code,
            turnstile_token=turnstile,
            castle_request_token="",
            conversion_id=str(uuid.uuid4()),
        )
        http_status = int(getattr(result, "http_status", 0) or 0)
        rsc_body = str(getattr(result, "rsc_body", "") or "")
        try:
            signup_error = str(client.extract_signup_error(rsc_body) or "")
        except Exception:
            signup_error = ""
        if http_status == 200 and not signup_error:
            context.update(RegistrationStage.CREATE_ACCOUNT, "xAI 账号创建成功")
            return result
        last_error = signup_error or f"HTTP {http_status}"
        retryable = http_status in {403, 408, 409, 425, 429} or http_status >= 500
        retryable = retryable or any(
            marker in last_error.lower()
            for marker in ("turnstile", "captcha", "validation-code", "expired", "rate")
        )
        if not retryable or attempt >= 3:
            raise RuntimeError(f"create_account rejected: {last_error}")
        time.sleep(1.5 * attempt)
    raise RuntimeError(f"create_account failed: {last_error}")


def _extract_sso(
    client: XConsoleAuthClient,
    result: Any,
    email: str,
    password: str,
    request: RegistrationRequest,
    context: RegistrationContext,
) -> str:
    context.update(RegistrationStage.SSO, "正在获取 SSO 会话")
    try:
        sso = client.fetch_sso_token(email=email, password=password, save=False, retries=5)
    except Exception:
        sso = None
    if sso:
        context.update(RegistrationStage.SSO, "SSO 会话已获取")
        return str(sso)

    set_cookies = list(getattr(result, "set_cookies", None) or [])
    rsc_body = str(getattr(result, "rsc_body", "") or "")
    try:
        from app.vendor.grok_build_auth.xconsole_client.sso import (
            parse_sso_from_set_cookies,
            parse_sso_token_from_text,
        )

        sso = parse_sso_from_set_cookies(set_cookies) or parse_sso_token_from_text(rsc_body)
    except Exception:
        sso = None
    if sso:
        context.update(RegistrationStage.SSO, "SSO 会话已获取")
        return str(sso)

    for attempt in range(1, 5):
        context.update(
            RegistrationStage.SSO,
            f"密码会话兜底 · 第 {attempt}/4 次",
        )
        if attempt == 1:
            time.sleep(2.5)
        token = _solve_turnstile(client, request, context, url=SIGNIN_URL)
        try:
            sso = client.obtain_session_via_password(
                email=email,
                password=password,
                turnstile_token=token,
                referer=SIGNIN_URL,
                retries=3,
            )
        except Exception:
            sso = None
        if sso:
            context.update(RegistrationStage.SSO, "SSO 会话已获取")
            return str(sso)
        time.sleep(min(8, 2 + attempt * 1.5))
    raise RuntimeError("account created but no usable SSO session was returned")


class RegistrationEngine:
    def register(
        self,
        request: RegistrationRequest,
        context: RegistrationContext,
        *,
        slot: int = 0,
    ) -> RegisteredAccount:
        client: XConsoleAuthClient | None = None
        context.update(RegistrationStage.MAILBOX, "正在创建临时邮箱")
        mailbox = create_registration_mailbox(
            provider=request.mail_provider,
            api_key=request.mail_api_key,
            base_url=request.mail_base_url,
            domains=request.mail_domain,
            slot=slot,
            proxy=request.proxy,
        )
        context.extra["email"] = mailbox.email
        context.update(RegistrationStage.MAILBOX, f"临时邮箱已就绪：{mailbox.email}")
        password = _password()
        try:
            context.update(RegistrationStage.SIGNUP_PAGE, "正在加载 xAI 注册协议")
            client = XConsoleAuthClient(
                debug=False,
                proxy=request.proxy or None,
                signup_url=SIGNUP_URL,
            )
            client.visit_home()
            client.load_signup_page()
            context.update(RegistrationStage.TURNSTILE, "正在进行 Turnstile 验证")
            turnstile = _solve_turnstile(client, request, context)
            context.update(RegistrationStage.TURNSTILE, "Turnstile 验证通过")
            try:
                client.validate_password(mailbox.email, password)
            except Exception:
                pass
            result = _create_account(
                client,
                mailbox,
                password,
                turnstile,
                request,
                context,
            )
            sso = _extract_sso(
                client,
                result,
                mailbox.email,
                password,
                request,
                context,
            )
            context.update(RegistrationStage.COMPLETED, "注册完成，SSO 已获取")
            return RegisteredAccount(email=mailbox.email, password=password, sso=sso)
        finally:
            if client is not None:
                client.close()