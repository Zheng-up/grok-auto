from __future__ import annotations

import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable

from app.db import Database
from app.services.events import EventLog
from app.services.settings import SettingsService


class OperationManager:
    def __init__(self, db: Database, events: EventLog, settings: SettingsService):
        self.db = db
        self.events = events
        self.settings = settings
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="operation")
        self._remote_pool = ThreadPoolExecutor(max_workers=50, thread_name_prefix="remote-operation")
        self._items = ThreadPoolExecutor(max_workers=50, thread_name_prefix="operation-item")
        self._remote_items = ThreadPoolExecutor(max_workers=50, thread_name_prefix="remote-item")
        self._handlers: dict[str, Callable[[str, str], Any]] = {}
        self._before_handlers: dict[str, Callable[[str], None]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._pause: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._slots = threading.Condition()
        self._active_items = 0

    def register(
        self,
        kind: str,
        handler: Callable[[str, str], Any],
        before: Callable[[str], None] | None = None,
    ) -> None:
        self._handlers[kind] = handler
        if before:
            self._before_handlers[kind] = before

    def start(self, kind: str, account_ids: list[str]) -> dict[str, Any]:
        if kind not in self._handlers:
            raise ValueError(f"unsupported operation: {kind}")
        unique_ids = list(dict.fromkeys(account_ids))
        if not unique_ids:
            raise ValueError("select at least one account")
        operation_id = f"op_{uuid.uuid4().hex[:16]}"
        concurrency = max(1, min(int(self.settings.get("registration_concurrency", 2) or 2), 50, len(unique_ids)))
        retry_limit = max(0, min(int(self.settings.get("registration_retry_limit", 1) or 0), 5))
        with self._lock, self.db.transaction() as conn:
            placeholders = ",".join("?" for _ in unique_ids)
            existing = {
                row[0]
                for row in conn.execute(
                    f"SELECT id FROM accounts WHERE id IN ({placeholders})",
                    unique_ids,
                ).fetchall()
            }
            missing = [account_id for account_id in unique_ids if account_id not in existing]
            if missing:
                raise ValueError("one or more selected accounts no longer exist")
            if kind in {"remote_sso", "remote_web"}:
                conflict_kinds = ["remote_sso", "remote_web"]
            else:
                conflict_kinds = [kind]
            kind_placeholders = ",".join("?" for _ in conflict_kinds)
            existing_operation = conn.execute(
                f"""
                SELECT job.id,COUNT(DISTINCT item.account_id) matched
                FROM operation_items item
                JOIN operation_jobs job ON job.id=item.operation_id
                WHERE job.kind IN ({kind_placeholders}) AND job.status IN ('queued','running','waiting','stopping','pausing','paused')
                  AND job.total=? AND item.account_id IN ({placeholders})
                GROUP BY job.id
                HAVING matched=?
                LIMIT 1
                """,
                (*conflict_kinds, len(unique_ids), *unique_ids, len(unique_ids)),
            ).fetchone()
            if existing_operation:
                running = self.get(str(existing_operation["id"])) or {"id": existing_operation["id"]}
                running["reused"] = True
                return running
            conflict = conn.execute(
                f"""
                SELECT 1 FROM operation_items item
                JOIN operation_jobs job ON job.id=item.operation_id
                WHERE job.kind IN ({kind_placeholders}) AND job.status IN ('queued','running','waiting','stopping','pausing','paused')
                  AND item.account_id IN ({placeholders})
                LIMIT 1
                """,
                (*conflict_kinds, *unique_ids),
            ).fetchone()
            if conflict:
                raise ValueError("部分所选账号已有同类操作正在执行")
            conn.execute(
                "INSERT INTO operation_jobs(id,kind,status,total) VALUES(?,?,'queued',?)",
                (operation_id, kind, len(unique_ids)),
            )
            conn.executemany(
                "INSERT INTO operation_items(operation_id,account_id,status) VALUES(?,?,'queued')",
                [(operation_id, account_id) for account_id in unique_ids],
            )
        cancel = threading.Event()
        pause = threading.Event()
        with self._lock:
            self._cancel[operation_id] = cancel
            self._pause[operation_id] = pause
        scheduler = self._remote_pool if kind in self._before_handlers else self._pool
        scheduler.submit(
            self._run,
            operation_id,
            kind,
            unique_ids,
            cancel,
            pause,
            concurrency,
            retry_limit,
        )
        return self.get(operation_id) or {"id": operation_id}

    def stop(self, operation_id: str) -> bool:
        operation = self.db.fetch_one("SELECT status FROM operation_jobs WHERE id=?", (operation_id,))
        if not operation or operation["status"] not in {"queued", "running", "waiting", "stopping", "pausing", "paused"}:
            return False
        if operation["status"] == "paused":
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE operation_items SET status='cancelled',message='任务已取消' WHERE operation_id=? AND status='queued'",
                    (operation_id,),
                )
                conn.execute(
                    "UPDATE operation_jobs SET cancel_requested=1,pause_requested=0,status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (operation_id,),
                )
            with self._lock:
                self._cancel.pop(operation_id, None)
                self._pause.pop(operation_id, None)
            self.events.publish(operation_id, "[!] 账号操作已取消", "warning")
            return True
        self.db.execute(
            "UPDATE operation_jobs SET cancel_requested=1,pause_requested=0,status='stopping',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (operation_id,),
        )
        with self._lock:
            cancel = self._cancel.get(operation_id)
            pause = self._pause.get(operation_id)
        if pause:
            pause.clear()
        if cancel:
            cancel.set()
        with self._slots:
            self._slots.notify_all()
        self.events.publish(operation_id, "[!] 正在停止账号操作", "warning")
        return True

    def pause(self, operation_id: str) -> bool:
        operation = self.db.fetch_one("SELECT status FROM operation_jobs WHERE id=?", (operation_id,))
        if not operation or operation["status"] not in {"queued", "running"}:
            return False
        self.db.execute(
            "UPDATE operation_jobs SET pause_requested=1,status='pausing',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (operation_id,),
        )
        with self._lock:
            pause = self._pause.get(operation_id)
        if pause:
            pause.set()
        with self._slots:
            self._slots.notify_all()
        self.events.publish(operation_id, "[!] 正在暂停账号操作", "warning")
        return True

    def resume(self, operation_id: str) -> bool:
        operation = self.db.fetch_one("SELECT kind,status FROM operation_jobs WHERE id=?", (operation_id,))
        if not operation or operation["status"] != "paused":
            return False
        rows = self.db.fetch_all(
            "SELECT account_id FROM operation_items WHERE operation_id=? AND status='queued' ORDER BY id",
            (operation_id,),
        )
        account_ids = [str(row["account_id"]) for row in rows]
        if not account_ids:
            return False
        concurrency = max(1, min(int(self.settings.get("registration_concurrency", 2) or 2), 50, len(account_ids)))
        retry_limit = max(0, min(int(self.settings.get("registration_retry_limit", 1) or 0), 5))
        with self._lock:
            cancel = self._cancel.setdefault(operation_id, threading.Event())
            pause = self._pause.setdefault(operation_id, threading.Event())
            cancel.clear()
            pause.clear()
        self.db.execute(
            "UPDATE operation_jobs SET cancel_requested=0,pause_requested=0,status='queued',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (operation_id,),
        )
        operation_kind = str(operation["kind"])
        scheduler = self._remote_pool if operation_kind in self._before_handlers else self._pool
        scheduler.submit(
            self._run,
            operation_id,
            operation_kind,
            account_ids,
            cancel,
            pause,
            concurrency,
            retry_limit,
        )
        self.events.publish(operation_id, "[*] 账号操作已继续")
        return True

    def queue_one(self, kind: str, account_id: str) -> None:
        try:
            self.start(kind, [account_id])
        except Exception as exc:
            self.events.publish(account_id, f"[-] 无法创建 {kind} 操作：{exc}", "error")

    def set_remote_waiting(self, operation_id: str, waiting: bool) -> None:
        if not operation_id.startswith("op_"):
            return
        changed = False
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT status FROM operation_jobs WHERE id=?",
                (operation_id,),
            ).fetchone()
            if not row:
                return
            current = str(row["status"])
            if waiting and current in {"queued", "running"}:
                conn.execute(
                    "UPDATE operation_jobs SET status='waiting',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (operation_id,),
                )
                conn.execute(
                    "UPDATE operation_items SET status='waiting',message='远端限流，等待冷却结束' WHERE operation_id=? AND status='running'",
                    (operation_id,),
                )
                changed = True
            elif not waiting and current == "waiting":
                conn.execute(
                    "UPDATE operation_jobs SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (operation_id,),
                )
                conn.execute(
                    "UPDATE operation_items SET status='running',message='' WHERE operation_id=? AND status='waiting'",
                    (operation_id,),
                )
                changed = True
        if changed and not waiting:
            self.events.publish(operation_id, "[*] 远端限流等待结束，任务继续执行")

    def _acquire_slot(self, limit: int, cancel: threading.Event, pause: threading.Event) -> bool:
        with self._slots:
            while self._active_items >= limit:
                if cancel.is_set() or pause.is_set():
                    return False
                self._slots.wait(timeout=0.2)
            if cancel.is_set() or pause.is_set():
                return False
            self._active_items += 1
            return True

    def _release_slot(self) -> None:
        with self._slots:
            self._active_items -= 1
            self._slots.notify_all()

    def _run_item(
        self,
        operation_id: str,
        account_id: str,
        handler: Callable[[str, str], Any],
        before: Callable[[str], None] | None,
        cancel: threading.Event,
        pause: threading.Event,
        concurrency: int,
        retry_limit: int,
    ) -> tuple[str, str]:
        attempt = 0
        while True:
            if cancel.is_set():
                return "cancelled", "任务已取消"
            if pause.is_set():
                return "queued", "等待继续"
            if before:
                before(operation_id)
                if cancel.is_set():
                    return "cancelled", "任务已取消"
                if pause.is_set():
                    return "queued", "等待继续"
            uses_local_slot = before is None
            if uses_local_slot and not self._acquire_slot(concurrency, cancel, pause):
                return ("cancelled", "任务开始前已取消") if cancel.is_set() else ("queued", "等待继续")
            try:
                self.db.execute(
                    "UPDATE operation_items SET status='running' WHERE operation_id=? AND account_id=?",
                    (operation_id, account_id),
                )
                handler(account_id, operation_id)
                return "success", "操作完成"
            except Exception as exc:
                if bool(getattr(exc, "remote_rate_limited", False)):
                    continue
                message = str(exc)[:1000]
                if attempt >= retry_limit:
                    return "failed", message
                attempt += 1
                self.events.publish(
                    operation_id,
                    f"[!] 账号操作失败，正在进行第 {attempt}/{retry_limit} 次重试",
                    "warning",
                )
            finally:
                if uses_local_slot:
                    self._release_slot()

    def _run(
        self,
        operation_id: str,
        kind: str,
        account_ids: list[str],
        cancel: threading.Event,
        pause: threading.Event,
        concurrency: int,
        retry_limit: int,
    ) -> None:
        self.db.execute(
            "UPDATE operation_jobs SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            ("pausing" if pause.is_set() else "running", operation_id),
        )
        self.events.publish(
            operation_id,
            f"[*] 账号操作开始 · 并发 {concurrency} · 失败重试 {retry_limit} 次",
        )
        handler = self._handlers[kind]
        before = self._before_handlers.get(kind)
        pending_ids = iter(account_ids)
        inflight: dict[Future[tuple[str, str]], str] = {}
        exhausted = False
        existing = self.db.fetch_one(
            "SELECT SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) success,SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed FROM operation_items WHERE operation_id=?",
            (operation_id,),
        ) or {}
        success = int(existing.get("success") or 0)
        failed = int(existing.get("failed") or 0)
        while inflight or not exhausted:
            while not cancel.is_set() and not pause.is_set() and not exhausted and len(inflight) < concurrency:
                try:
                    account_id = next(pending_ids)
                except StopIteration:
                    exhausted = True
                    break
                item_pool = self._remote_items if before else self._items
                future = item_pool.submit(
                    self._run_item,
                    operation_id,
                    account_id,
                    handler,
                    before,
                    cancel,
                    pause,
                    concurrency,
                    retry_limit,
                )
                inflight[future] = account_id
            if not inflight:
                break
            done, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for future in done:
                account_id = inflight.pop(future)
                try:
                    status, message = future.result()
                except Exception:
                    status, message = "failed", "账号操作执行异常"
                if status == "success":
                    success += 1
                elif status == "failed":
                    failed += 1
                self.db.execute(
                    "UPDATE operation_items SET status=?,message=? WHERE operation_id=? AND account_id=?",
                    (status, message, operation_id, account_id),
                )
                if status == "success":
                    try:
                        self._resolve_prior_failures(kind, account_id, operation_id)
                    except Exception:
                        self.events.publish(
                            operation_id,
                            "[!] 当前操作已成功，但历史失败任务状态同步失败",
                            "warning",
                        )
                self.db.execute(
                    "UPDATE operation_jobs SET completed=?,success=?,failed=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (success + failed, success, failed, operation_id),
                )
        remaining = self.db.fetch_one(
            "SELECT COUNT(*) total FROM operation_items WHERE operation_id=? AND status='queued'",
            (operation_id,),
        ) or {}
        has_remaining = int(remaining.get("total") or 0) > 0
        if cancel.is_set():
            self.db.execute(
                "UPDATE operation_items SET status='cancelled',message='任务开始前已取消' WHERE operation_id=? AND status='queued'",
                (operation_id,),
            )
            final = "cancelled"
        elif pause.is_set() and has_remaining:
            final = "paused"
        else:
            final = "completed" if failed == 0 else ("failed" if success == 0 else "partial")
        self.db.execute(
            "UPDATE operation_jobs SET status=?,pause_requested=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (final, int(final == "paused"), operation_id),
        )
        final_label = {"completed": "已完成", "partial": "部分成功", "failed": "失败", "cancelled": "已取消", "paused": "已暂停"}.get(final, final)
        level = "success" if final == "completed" else ("error" if final == "failed" else "warning")
        prefix = {"success": "[+]", "warning": "[!]", "error": "[-]"}[level]
        self.events.publish(operation_id, f"{prefix} 账号操作{final_label} · 成功 {success} · 失败 {failed}", level)
        if final != "paused":
            with self._lock:
                self._cancel.pop(operation_id, None)
                self._pause.pop(operation_id, None)

    def _resolve_prior_failures(
        self,
        kind: str,
        account_id: str,
        operation_id: str,
    ) -> None:
        equivalent_kinds = ("remote_sso", "remote_web") if kind in {"remote_sso", "remote_web"} else (kind,)
        kind_placeholders = ",".join("?" for _ in equivalent_kinds)
        resolved_jobs: list[str] = []
        with self.db.transaction() as conn:
            current_item = conn.execute(
                "SELECT id FROM operation_items WHERE operation_id=? AND account_id=?",
                (operation_id, account_id),
            ).fetchone()
            if not current_item:
                return
            rows = conn.execute(
                f"""
                SELECT DISTINCT old_job.id,old_job.status
                FROM operation_items old_item
                JOIN operation_jobs old_job ON old_job.id=old_item.operation_id
                WHERE old_item.account_id=?
                  AND old_item.id<?
                  AND old_item.status IN ('failed','interrupted')
                  AND old_job.kind IN ({kind_placeholders})
                """,
                (account_id, int(current_item["id"]), *equivalent_kinds),
            ).fetchall()
            if not rows:
                return
            old_job_ids = [str(row["id"]) for row in rows]
            job_placeholders = ",".join("?" for _ in old_job_ids)
            conn.execute(
                f"""
                UPDATE operation_items
                SET status='resolved',
                    message=CASE
                        WHEN message='' THEN '后续同类操作已成功'
                        ELSE message || '；后续同类操作已成功'
                    END
                WHERE account_id=?
                  AND id<?
                  AND status IN ('failed','interrupted')
                  AND operation_id IN ({job_placeholders})
                """,
                (account_id, int(current_item["id"]), *old_job_ids),
            )
            for row in rows:
                old_job_id = str(row["id"])
                counts = conn.execute(
                    """
                    SELECT
                      SUM(CASE WHEN status IN ('success','resolved') THEN 1 ELSE 0 END) succeeded,
                      SUM(CASE WHEN status IN ('failed','interrupted') THEN 1 ELSE 0 END) failed,
                      SUM(CASE WHEN status IN ('success','resolved','failed','interrupted') THEN 1 ELSE 0 END) completed
                    FROM operation_items WHERE operation_id=?
                    """,
                    (old_job_id,),
                ).fetchone()
                succeeded = int(counts["succeeded"] or 0)
                failed = int(counts["failed"] or 0)
                completed = int(counts["completed"] or 0)
                if failed == 0:
                    status = "resolved"
                    resolved_jobs.append(old_job_id)
                elif str(row["status"]) == "retried":
                    status = "retried"
                elif succeeded:
                    status = "partial"
                else:
                    status = "failed"
                conn.execute(
                    "UPDATE operation_jobs SET status=?,completed=?,success=?,failed=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (status, completed, succeeded, failed, old_job_id),
                )
        for old_job_id in resolved_jobs:
            self.events.publish(
                old_job_id,
                "[+] 此任务的失败项已由后续同类操作成功恢复",
                "success",
            )

    def retry(self, operation_id: str) -> dict[str, Any]:
        operation = self.get(operation_id)
        if not operation:
            raise ValueError("operation not found")
        if operation["status"] in {"queued", "running", "waiting", "stopping", "pausing", "paused"}:
            raise ValueError("running operation cannot be retried")
        retryable_ids = [
            item["account_id"]
            for item in operation.get("items", [])
            if item["status"] in {"failed", "interrupted"}
        ]
        if not retryable_ids:
            raise ValueError("operation has no retryable items")
        retried = self.start(str(operation["kind"]), retryable_ids)
        self.db.execute(
            "UPDATE operation_jobs SET status='retried',updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (operation_id,),
        )
        return retried

    def close(self) -> None:
        with self._lock:
            for cancel in self._cancel.values():
                cancel.set()
            for pause in self._pause.values():
                pause.clear()
        with self._slots:
            self._slots.notify_all()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._remote_pool.shutdown(wait=False, cancel_futures=True)
        self._items.shutdown(wait=False, cancel_futures=True)
        self._remote_items.shutdown(wait=False, cancel_futures=True)

    def list(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM operation_jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (max(1, min(limit, 500)), max(0, offset)),
        )

    def count(self) -> int:
        row = self.db.fetch_one("SELECT COUNT(*) total FROM operation_jobs")
        return int((row or {}).get("total") or 0)

    def get(self, operation_id: str) -> dict[str, Any] | None:
        operation = self.db.fetch_one("SELECT * FROM operation_jobs WHERE id=?", (operation_id,))
        if operation:
            operation["items"] = self.db.fetch_all(
                "SELECT account_id,status,message FROM operation_items WHERE operation_id=? ORDER BY id",
                (operation_id,),
            )
        return operation