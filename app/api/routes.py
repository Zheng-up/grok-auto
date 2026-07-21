from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    AccountOperationRequest,
    AccountSelectionRequest,
    AdminInitRequest,
    DeleteAccountsRequest,
    LoginRequest,
    RegistrationStartRequest,
    SettingsUpdateRequest,
)
from app.config import runtime
from app.runtime import services

router = APIRouter(prefix="/api")
SESSION_COOKIE = "reg_console_session"


def require_admin(reg_console_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = services.auth.authenticate(reg_console_session)
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


@router.get("/auth/status")
def auth_status(reg_console_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = services.auth.authenticate(reg_console_session)
    return {
        "initialized": services.auth.is_initialized(),
        "authenticated": bool(user),
        "user": {"id": user["id"], "username": user["username"]} if user else None,
    }


@router.post("/auth/initialize", status_code=201)
def initialize_admin(body: AdminInitRequest) -> dict[str, Any]:
    try:
        services.auth.initialize_admin(body.username, body.password)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/auth/login")
def login(body: LoginRequest, response: Response) -> dict[str, Any]:
    try:
        token, expires = services.auth.login(body.username, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=runtime.cookie_secure,
        samesite="strict",
        expires=expires,
        path="/",
    )
    return {"ok": True, "username": body.username}


@router.post("/auth/logout")
def logout(
    response: Response,
    reg_console_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    services.auth.logout(reg_console_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/dashboard")
def dashboard(_: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    accounts = services.db.fetch_one(
        """
        SELECT COUNT(*) total,
               SUM(CASE WHEN oidc_status='success' THEN 1 ELSE 0 END) oidc_ready,
               SUM(CASE WHEN remote_web_status='success' THEN 1 ELSE 0 END) remote_web_ready,
               SUM(CASE WHEN remote_build_status='success' THEN 1 ELSE 0 END) remote_build_ready,
               SUM(CASE WHEN remote_console_status='success' THEN 1 ELSE 0 END) remote_console_ready
        FROM accounts
        """
    ) or {}
    today = services.db.fetch_one(
        """
        SELECT COUNT(*) total,
               SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) success,
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed,
               ROUND(AVG(CASE WHEN started_at IS NOT NULL AND status IN ('success','failed','cancelled','interrupted') THEN (julianday(updated_at)-julianday(started_at))*86400 END)) average_seconds,
               ROUND(SUM(CASE WHEN started_at IS NOT NULL AND status IN ('success','failed','cancelled','interrupted') THEN (julianday(updated_at)-julianday(started_at))*86400 ELSE 0 END)) total_seconds
        FROM registration_jobs
        WHERE date(created_at)=date('now','localtime')
        """
    ) or {}
    active = services.db.fetch_one(
        """
        SELECT
          (SELECT COUNT(*) FROM registration_batches WHERE status IN ('queued','running','stopping','pausing','paused')) active_batches,
          (SELECT COUNT(*) FROM operation_jobs WHERE status IN ('queued','running','stopping','pausing','paused')) active_operations
        """
    ) or {}
    return {
        "accounts": {key: int(value or 0) for key, value in accounts.items()},
        "today": {key: int(value or 0) for key, value in today.items()},
        "active": {key: int(value or 0) for key, value in active.items()},
        "recent_batches": services.registration.list_batches(5),
        "recent_operations": services.operations.list(5),
    }


@router.get("/settings")
def get_settings(
    response: Response,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return services.settings.get_all(reveal_secrets=True)


@router.put("/settings")
def update_settings(
    body: SettingsUpdateRequest,
    response: Response,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return services.settings.set_many(body.values)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/settings/test-remote")
def test_remote(_: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        return services.remote.test_connection()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/registration/batches", status_code=202)
def start_registration(
    body: RegistrationStartRequest,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return services.registration.start(body.count, body.concurrency, body.overrides)


@router.get("/registration/batches")
def list_batches(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: dict[str, Any] = Depends(require_admin),
) -> list[dict[str, Any]]:
    return services.registration.list_batches(limit, offset)


@router.get("/registration/batches/count")
def count_batches(_: dict[str, Any] = Depends(require_admin)) -> dict[str, int]:
    return {"total": services.registration.count_batches()}


@router.get("/registration/batches/{batch_id}")
def get_batch(batch_id: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    batch = services.registration.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


@router.post("/registration/batches/{batch_id}/pause")
def pause_batch(batch_id: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if not services.registration.pause(batch_id):
        raise HTTPException(status_code=409, detail="batch cannot be paused")
    return {"ok": True}


@router.post("/registration/batches/{batch_id}/resume")
def resume_batch(batch_id: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if not services.registration.resume(batch_id):
        raise HTTPException(status_code=409, detail="batch cannot be resumed")
    return {"ok": True}


@router.post("/registration/batches/{batch_id}/stop")
def stop_batch(batch_id: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if not services.registration.stop(batch_id):
        raise HTTPException(status_code=409, detail="batch is not running")
    return {"ok": True}


@router.post("/registration/batches/{batch_id}/retry", status_code=202)
def retry_batch(batch_id: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        return services.registration.retry(batch_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "batch not found" else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/accounts")
def list_accounts(
    q: str = "",
    register_status: str = "",
    oidc_status: str = "",
    remote_web_status: str = "",
    remote_build_status: str = "",
    remote_console_status: str = "",
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    _: dict[str, Any] = Depends(require_admin),
) -> list[dict[str, Any]]:
    return services.accounts.list(
        q,
        limit,
        offset,
        register_status,
        oidc_status,
        remote_web_status,
        remote_build_status,
        remote_console_status,
    )


@router.get("/accounts/count")
def count_accounts(
    q: str = "",
    register_status: str = "",
    oidc_status: str = "",
    remote_web_status: str = "",
    remote_build_status: str = "",
    remote_console_status: str = "",
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, int]:
    return {
        "total": services.accounts.count(
            q,
            register_status,
            oidc_status,
            remote_web_status,
            remote_build_status,
            remote_console_status,
        )
    }


@router.get("/accounts/{account_id}")
def get_account(account_id: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    account = services.accounts.get(account_id, reveal=False)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    return account


@router.post("/accounts/operations", status_code=202)
def start_account_operation(
    body: AccountOperationRequest,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return services.operations.start(body.kind, body.account_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/accounts")
def delete_accounts(
    body: DeleteAccountsRequest,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return {"deleted": services.accounts.delete(body.account_ids)}


@router.get("/operations")
def list_operations(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: dict[str, Any] = Depends(require_admin),
) -> list[dict[str, Any]]:
    return services.operations.list(limit, offset)


@router.get("/operations/count")
def count_operations(_: dict[str, Any] = Depends(require_admin)) -> dict[str, int]:
    return {"total": services.operations.count()}


@router.post("/operations/{operation_id}/pause")
def pause_operation(
    operation_id: str,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not services.operations.pause(operation_id):
        raise HTTPException(status_code=409, detail="operation cannot be paused")
    return {"ok": True}


@router.post("/operations/{operation_id}/resume")
def resume_operation(
    operation_id: str,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not services.operations.resume(operation_id):
        raise HTTPException(status_code=409, detail="operation cannot be resumed")
    return {"ok": True}


@router.post("/operations/{operation_id}/stop")
def stop_operation(
    operation_id: str,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not services.operations.stop(operation_id):
        raise HTTPException(status_code=409, detail="operation is not running")
    return {"ok": True}


@router.post("/operations/{operation_id}/retry", status_code=202)
def retry_operation(
    operation_id: str,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return services.operations.retry(operation_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "operation not found" else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/operations/{operation_id}")
def get_operation(operation_id: str, _: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    operation = services.operations.get(operation_id)
    if not operation:
        raise HTTPException(status_code=404, detail="operation not found")
    return operation


def _download(data: bytes, filename: str, media_type: str) -> Response:
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/exports/tokens")
def export_tokens(
    body: AccountSelectionRequest,
    _: dict[str, Any] = Depends(require_admin),
) -> Response:
    return _download(services.exports.tokens_txt(body.account_ids or None), "tokens.txt", "text/plain; charset=utf-8")


@router.post("/exports/accounts")
def export_accounts(
    body: AccountSelectionRequest,
    _: dict[str, Any] = Depends(require_admin),
) -> Response:
    return _download(services.exports.accounts_txt(body.account_ids or None), "accounts.txt", "text/plain; charset=utf-8")


@router.post("/exports/cpa")
def export_cpa(
    body: AccountSelectionRequest,
    _: dict[str, Any] = Depends(require_admin),
) -> Response:
    return _download(services.exports.cpa_zip(body.account_ids or None), "auths.zip", "application/zip")


@router.get("/logs/{stream_id}")
def get_logs(
    stream_id: str,
    after: int = Query(default=0, ge=0),
    _: dict[str, Any] = Depends(require_admin),
) -> list[dict[str, Any]]:
    return services.events.read(stream_id, after)


@router.delete("/logs")
def clear_all_logs(
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, int]:
    try:
        return services.events.clear_all()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/logs/{stream_id}")
def clear_logs(
    stream_id: str,
    _: dict[str, Any] = Depends(require_admin),
) -> dict[str, int]:
    return {"deleted": services.events.clear(stream_id)}


@router.get("/events/{stream_id}")
def event_stream(
    stream_id: str,
    after: int = Query(default=0, ge=0),
    _: dict[str, Any] = Depends(require_admin),
) -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        cursor = after
        while True:
            rows = services.events.read(stream_id, cursor)
            if rows:
                for row in rows:
                    cursor = max(cursor, int(row["id"]))
                    yield f"id: {row['id']}\nevent: log\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream")