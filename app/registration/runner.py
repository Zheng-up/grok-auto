from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable

from app.config import runtime
from app.db import Database
from app.redaction import redact_error
from app.registration.engine import RegistrationEngine
from app.registration.models import (
    RegistrationCancelled,
    RegistrationContext,
    RegistrationRequest,
    RegistrationStage,
)
from app.services.accounts import AccountService
from app.services.events import EventLog
from app.services.settings import SettingsService
from grok2api.upstream.proxy_pool import parse_proxy_pool, pick_proxy


def _progress_level(stage: RegistrationStage, message: str) -> str:
    if stage == RegistrationStage.COMPLETED or any(
        marker in message for marker in ("已就绪", "已获取", "成功", "完成", "验证通过")
    ):
        return "success"
    if any(marker in message for marker in ("等待", "重试", "兜底", "刷新")):
        return "warning"
    return "info"


def _log_prefix(level: str) -> str:
    return {"success": "[+]", "warning": "[!]", "error": "[-]"}.get(level, "[*]")


class RegistrationRunner:
    def __init__(
        self,
        db: Database,
        settings: SettingsService,
        accounts: AccountService,
        events: EventLog,
        queue_operation: Callable[[str, str], None] | None = None,
    ):
        self.db = db
        self.settings = settings
        self.accounts = accounts
        self.events = events
        self.engine = RegistrationEngine()
        self.queue_operation = queue_operation
        self._cancel: dict[str, threading.Event] = {}
        self._pause: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def start(self, count: int | None, concurrency: int | None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = self.settings.registration_config()
        cfg.update(overrides or {})
        count = max(1, min(int(count if count is not None else cfg.get("registration_count") or 1), 25000))
        concurrency = max(
            1,
            min(
                int(concurrency if concurrency is not None else cfg.get("registration_concurrency") or 2),
                runtime.registration_max_concurrency,
                count,
            ),
        )
        if cfg.get("captcha_provider") == "local":
            concurrency = min(concurrency, runtime.local_solver_max_concurrency)
        batch_id = f"batch_{uuid.uuid4().hex[:14]}"
        public_cfg = {
            key: value
            for key, value in cfg.items()
            if key not in {"mail_api_key", "captcha_api_key", "proxy_pool", "remote_secret"}
        }
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO registration_batches(id,status,target_count,concurrency,config_json) VALUES(?,'queued',?,?,?)",
                (batch_id, count, concurrency, json.dumps(public_cfg, ensure_ascii=False)),
            )
            conn.executemany(
                "INSERT INTO registration_jobs(id,batch_id,slot,status,stage) VALUES(?, ?, ?, 'queued', 'queued')",
                [
                    (f"job_{uuid.uuid4().hex[:16]}", batch_id, slot)
                    for slot in range(count)
                ],
            )
        cancel = threading.Event()
        pause = threading.Event()
        with self._lock:
            self._cancel[batch_id] = cancel
            self._pause[batch_id] = pause
        self.events.publish(batch_id, f"[*] 注册批次已创建 · 数量 {count} · 并发 {concurrency}")
        threading.Thread(
            target=self._run_batch,
            args=(batch_id, cfg, concurrency, cancel, pause),
            daemon=True,
            name=f"registration-{batch_id[-8:]}",
        ).start()
        return self.get_batch(batch_id) or {"id": batch_id}

    def stop(self, batch_id: str) -> bool:
        batch = self.db.fetch_one("SELECT * FROM registration_batches WHERE id=?", (batch_id,))
        if not batch or batch["status"] not in {"queued", "running", "stopping", "pausing", "paused"}:
            return False
        if batch["status"] == "paused":
            self.db.execute(
                "UPDATE registration_jobs SET status='cancelled',stage='cancelled',message='任务已取消',updated_at=CURRENT_TIMESTAMP WHERE batch_id=? AND status='queued'",
                (batch_id,),
            )
            self.db.execute(
                "UPDATE registration_batches SET cancel_requested=1,pause_requested=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (batch_id,),
            )
            self._refresh_batch_counts(batch_id, final=True)
            with self._lock:
                self._cancel.pop(batch_id, None)
                self._pause.pop(batch_id, None)
            return True
        self.db.execute(
            "UPDATE registration_batches SET cancel_requested=1,pause_requested=0,status='stopping',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (batch_id,),
        )
        with self._lock:
            event = self._cancel.get(batch_id)
            pause = self._pause.get(batch_id)
        if pause:
            pause.clear()
        if event:
            event.set()
        self.events.publish(batch_id, "[!] 正在停止注册批次", "warning")
        return True

    def pause(self, batch_id: str) -> bool:
        batch = self.db.fetch_one("SELECT status FROM registration_batches WHERE id=?", (batch_id,))
        if not batch or batch["status"] not in {"queued", "running"}:
            return False
        self.db.execute(
            "UPDATE registration_batches SET pause_requested=1,status='pausing',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (batch_id,),
        )
        with self._lock:
            pause = self._pause.get(batch_id)
        if pause:
            pause.set()
        self.events.publish(batch_id, "[!] 正在暂停注册批次，等待当前账号处理完成", "warning")
        return True

    def resume(self, batch_id: str) -> bool:
        batch = self.get_batch(batch_id)
        if not batch or batch["status"] != "paused":
            return False
        if not any(job["status"] == "queued" for job in batch["jobs"]):
            return False
        cfg = self.settings.registration_config()
        cfg.update(batch.get("config") or {})
        concurrency = max(1, min(int(batch["concurrency"]), runtime.registration_max_concurrency))
        with self._lock:
            cancel = self._cancel.setdefault(batch_id, threading.Event())
            pause = self._pause.setdefault(batch_id, threading.Event())
            cancel.clear()
            pause.clear()
        self.db.execute(
            "UPDATE registration_batches SET cancel_requested=0,pause_requested=0,status='queued',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (batch_id,),
        )
        threading.Thread(
            target=self._run_batch,
            args=(batch_id, cfg, concurrency, cancel, pause),
            daemon=True,
            name=f"registration-{batch_id[-8:]}",
        ).start()
        self.events.publish(batch_id, "[*] 注册批次已继续")
        return True

    def retry(self, batch_id: str) -> dict[str, Any]:
        batch = self.get_batch(batch_id)
        if not batch:
            raise ValueError("batch not found")
        if batch["status"] in {"queued", "running", "stopping", "pausing", "paused"}:
            raise ValueError("running batch cannot be retried")
        retryable = sum(
            1
            for job in batch["jobs"]
            if job["status"] in {"failed", "cancelled", "interrupted"}
        )
        if retryable == 0:
            raise ValueError("batch has no retryable jobs")
        overrides = dict(batch.get("config") or {})
        overrides["retry_of"] = batch_id
        retried = self.start(retryable, int(batch["concurrency"]), overrides)
        self.db.execute(
            "UPDATE registration_batches SET status='retried',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (batch_id,),
        )
        return retried

    def close(self) -> None:
        with self._lock:
            for cancel in self._cancel.values():
                cancel.set()
            for pause in self._pause.values():
                pause.clear()

    def list_batches(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT id,status,target_count,concurrency,completed,success,failed,cancel_requested,pause_requested,error,created_at,updated_at FROM registration_batches ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (max(1, min(limit, 500)), max(0, offset)),
        )

    def count_batches(self) -> int:
        row = self.db.fetch_one("SELECT COUNT(*) total FROM registration_batches")
        return int((row or {}).get("total") or 0)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        batch = self.db.fetch_one("SELECT * FROM registration_batches WHERE id=?", (batch_id,))
        if not batch:
            return None
        batch["config"] = json.loads(batch.pop("config_json") or "{}")
        batch["jobs"] = self.db.fetch_all(
            """
            SELECT j.id,j.slot,j.status,j.stage,j.message,j.email,j.account_id,j.error,
                   j.started_at,j.created_at,j.updated_at,a.oidc_status,a.oidc_error
            FROM registration_jobs j
            LEFT JOIN accounts a ON a.id=j.account_id
            WHERE j.batch_id=?
            ORDER BY j.slot
            """,
            (batch_id,),
        )
        return batch

    def _run_batch(
        self,
        batch_id: str,
        cfg: dict[str, Any],
        concurrency: int,
        cancel: threading.Event,
        pause: threading.Event,
    ) -> None:
        self.db.execute(
            "UPDATE registration_batches SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            ("pausing" if pause.is_set() else "running", batch_id),
        )
        jobs = self.db.fetch_all(
            "SELECT id,slot FROM registration_jobs WHERE batch_id=? AND status='queued' ORDER BY slot",
            (batch_id,),
        )
        try:
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="reg-worker") as pool:
                pending: dict[Any, dict[str, Any]] = {}
                cursor = iter(jobs)
                exhausted = False
                while pending or not exhausted:
                    while not cancel.is_set() and not pause.is_set() and len(pending) < concurrency and not exhausted:
                        try:
                            job = next(cursor)
                        except StopIteration:
                            exhausted = True
                            break
                        pending[pool.submit(self._run_job, batch_id, job, cfg, cancel)] = job
                    if not pending:
                        break
                    done, _ = wait(set(pending), timeout=0.5, return_when=FIRST_COMPLETED)
                    for future in done:
                        pending.pop(future, None)
                        try:
                            future.result()
                        except Exception as exc:
                            self.events.publish(batch_id, f"[-] 注册线程异常：{exc}", "error")
                    self._refresh_batch_counts(batch_id)
                    if pause.is_set() and not cancel.is_set():
                        self.db.execute(
                            "UPDATE registration_batches SET status='pausing',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (batch_id,),
                        )
                if cancel.is_set():
                    self.db.execute(
                        "UPDATE registration_jobs SET status='cancelled',stage='cancelled',message='任务开始前已取消',updated_at=CURRENT_TIMESTAMP WHERE batch_id=? AND status='queued'",
                        (batch_id,),
                    )
        finally:
            remaining = self.db.fetch_one(
                "SELECT COUNT(*) total FROM registration_jobs WHERE batch_id=? AND status='queued'",
                (batch_id,),
            ) or {}
            paused = pause.is_set() and not cancel.is_set() and int(remaining.get("total") or 0) > 0
            self._refresh_batch_counts(batch_id, final=not paused, paused=paused)
            if not paused:
                with self._lock:
                    self._cancel.pop(batch_id, None)
                    self._pause.pop(batch_id, None)

    def _run_job(
        self,
        batch_id: str,
        job: dict[str, Any],
        cfg: dict[str, Any],
        cancel: threading.Event,
    ) -> None:
        job_id = job["id"]
        slot = int(job["slot"])
        self.db.execute(
            "UPDATE registration_jobs SET status='running',stage='mailbox',message='正在启动注册任务',started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )
        proxy_pool = parse_proxy_pool(str(cfg.get("proxy_pool") or ""), fallback_env=False)
        proxy = pick_proxy(
            proxy_pool,
            strategy=str(cfg.get("proxy_strategy") or "round_robin"),
            index=slot,
        ) if proxy_pool else ""
        request = RegistrationRequest(
            mail_provider=str(cfg.get("mail_provider") or "cfmail"),
            mail_api_key=str(cfg.get("mail_api_key") or ""),
            mail_base_url=str(cfg.get("mail_base_url") or ""),
            mail_domain=str(cfg.get("mail_domains") or ""),
            captcha_provider=str(cfg.get("captcha_provider") or "local"),
            captcha_api_key=str(cfg.get("captcha_api_key") or ""),
            local_solver_url=str(cfg.get("local_solver_url") or "http://127.0.0.1:5072"),
            proxy=str(proxy or ""),
            mail_poll_timeout=max(30, min(int(cfg.get("mail_poll_timeout") or 180), 600)),
            retry_limit=max(0, min(int(cfg.get("registration_retry_limit") or 0), 5)),
        )
        extra: dict[str, Any] = {}
        current_stage = RegistrationStage.MAILBOX
        last_logged_stage: RegistrationStage | None = None
        last_logged_message = ""

        def progress(stage: RegistrationStage, message: str) -> None:
            nonlocal current_stage, last_logged_stage, last_logged_message
            current_stage = stage
            email = str(extra.get("email") or "") or None
            self.db.execute(
                "UPDATE registration_jobs SET stage=?,message=?,email=COALESCE(?,email),updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (stage.value, message, email, job_id),
            )
            level = _progress_level(stage, message)
            repeated_wait = message.startswith("正在等待") and last_logged_message.startswith("正在等待")
            should_log = (
                stage != last_logged_stage
                or level == "success"
                or (level == "warning" and not repeated_wait)
            )
            if should_log and message != last_logged_message:
                self.events.publish(
                    batch_id,
                    f"{_log_prefix(level)} 账号 #{slot + 1} · {message}",
                    level,
                )
                last_logged_stage = stage
                last_logged_message = message

        context = RegistrationContext(progress=progress, cancelled=cancel.is_set, extra=extra)
        try:
            attempt = 0
            while True:
                try:
                    account = self.engine.register(request, context, slot=slot)
                    break
                except RegistrationCancelled:
                    raise
                except Exception as exc:
                    unsafe_to_retry = current_stage in {
                        RegistrationStage.CREATE_ACCOUNT,
                        RegistrationStage.SSO,
                        RegistrationStage.COMPLETED,
                    }
                    if attempt >= request.retry_limit or unsafe_to_retry:
                        raise
                    attempt += 1
                    extra.clear()
                    safe_retry_error = redact_error(
                        exc,
                        (cfg.get("mail_api_key"), cfg.get("captcha_api_key"), proxy),
                    )
                    self.events.publish(
                        batch_id,
                        f"[!] 账号 #{slot + 1} · 创建前失败，准备重试 {attempt}/{request.retry_limit}：{safe_retry_error}",
                        "warning",
                    )
            account_id = self.accounts.save_registered(account, job_id)
            self.db.execute(
                "UPDATE registration_jobs SET status='success',stage='completed',message='注册完成，SSO 已获取',email=?,account_id=?,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (account.email, account_id, job_id),
            )
            self.events.publish(batch_id, f"[+] 账号 #{slot + 1} 注册成功：{account.email}", "success")
            if self.queue_operation:
                if bool(cfg.get("oidc_auto_mint", True)):
                    self.queue_operation("oidc", account_id)
                if bool(cfg.get("remote_web_auto_push", False)):
                    self.queue_operation("remote_web", account_id)
                if bool(cfg.get("remote_console_auto_push", False)):
                    self.queue_operation("remote_console", account_id)
        except RegistrationCancelled:
            self.db.execute(
                "UPDATE registration_jobs SET status='cancelled',stage='cancelled',message='注册任务已取消',error='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job_id,),
            )
            self.events.publish(batch_id, f"[!] 账号 #{slot + 1} 注册任务已取消", "warning")
        except Exception as exc:
            safe_error = redact_error(
                exc,
                (
                    cfg.get("mail_api_key"),
                    cfg.get("captcha_api_key"),
                    cfg.get("proxy_pool"),
                    proxy,
                ),
            )
            self.db.execute(
                "UPDATE registration_jobs SET status='failed',stage='failed',message=?,error=?,email=COALESCE(?,email),updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (safe_error[:500], safe_error, extra.get("email"), job_id),
            )
            self.events.publish(batch_id, f"[-] 账号 #{slot + 1} 注册失败：{safe_error}", "error")

    def _refresh_batch_counts(self, batch_id: str, final: bool = False, paused: bool = False) -> None:
        counts = self.db.fetch_one(
            "SELECT COUNT(*) completed, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) success, SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed, SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) cancelled FROM registration_jobs WHERE batch_id=? AND status IN ('success','failed','cancelled','interrupted')",
            (batch_id,),
        ) or {}
        batch = self.db.fetch_one("SELECT * FROM registration_batches WHERE id=?", (batch_id,)) or {}
        completed = int(counts.get("completed") or 0)
        success = int(counts.get("success") or 0)
        failed = int(counts.get("failed") or 0)
        if paused:
            status = "paused"
        elif final:
            status = "cancelled" if batch.get("cancel_requested") else "completed"
            if success == 0 and failed:
                status = "failed"
            elif failed:
                status = "partial"
        else:
            status = "stopping" if batch.get("cancel_requested") else "running"
        self.db.execute(
            "UPDATE registration_batches SET status=?,completed=?,success=?,failed=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, completed, success, failed, batch_id),
        )
        if final:
            status_label = {
                "completed": "全部完成",
                "partial": "部分完成",
                "failed": "全部失败",
                "cancelled": "已取消",
            }.get(status, status)
            level = "success" if status == "completed" else "warning"
            self.events.publish(
                batch_id,
                f"{_log_prefix(level)} 注册批次{status_label} · 已处理 {completed} · 成功 {success} · 失败 {failed}",
                level,
            )