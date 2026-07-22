from __future__ import annotations

import json
import urllib.error
import urllib.request
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



def _notify_local_solver(cfg: dict[str, Any], concurrency: int) -> None:
    """Warm Camoufox and set token prefetch depth = registration concurrency.

    Best-effort only — registration continues even if solver is busy/unreachable.
    """
    if str(cfg.get("captcha_provider") or "local").lower() != "local":
        return
    base = str(cfg.get("local_solver_url") or "http://127.0.0.1:5072").rstrip("/")
    if not base:
        return
    depth = max(1, min(20, int(concurrency or 1)))
    # Public sitekey used by signup; dynamic scrape may differ but this is good enough
    # for warming and typical prefetches. createTask still works without prefetch.
    try:
        from app.vendor.grok_build_auth.xconsole_client import config as protocol_config
        sitekey = str(getattr(protocol_config, "TURNSTILE_SITEKEY", "") or "").strip()
        signup_url = str(getattr(protocol_config, "SIGNUP_URL", "") or "https://accounts.x.ai/sign-up?redirect=cloud-console").strip()
    except Exception:
        sitekey = "0x4AAAAAAAhr9JGVDZbrZOo0"
        signup_url = "https://accounts.x.ai/sign-up?redirect=cloud-console"

    def _post(path: str, payload: dict[str, Any] | None = None) -> None:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if payload is not None else {},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()

    try:
        _post("/warm", {})
    except Exception:
        pass
    if not sitekey:
        return
    try:
        _post(
            "/prefetch",
            {
                "websiteURL": signup_url,
                "websiteKey": sitekey,
                "depth": depth,
            },
        )
    except Exception:
        pass

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
        self._ACTIVE_STATUSES = {"queued", "running", "stopping", "pausing"}
        self._slot_cond = threading.Condition(self._lock)
        self._active_account_slots = 0

    def global_concurrency(self) -> int:
        raw = int(self.settings.get("registration_concurrency", 2) or 2)
        return max(1, min(raw, runtime.registration_max_concurrency, 50))

    def set_global_concurrency(self, value: int) -> int:
        value = max(1, min(int(value), runtime.registration_max_concurrency, 50))
        # Persist for multi-batch shared slots (start page owns this value).
        self.settings.set_many({"registration_concurrency": value})
        with self._slot_cond:
            self._slot_cond.notify_all()
        return value

    def has_active_registration(self) -> bool:
        return bool(self._active_registration_ids())

    def _older_queued_demand(self, batch_id: str) -> int:
        """How many still-queued jobs belong to older *worker-active* batches.

        Only batches that currently have a live worker can reserve free slots.
        Pure waiting/paused batches must not hoard slots without consuming them.
        """
        batch = self.db.fetch_one(
            "SELECT created_at FROM registration_batches WHERE id=?",
            (batch_id,),
        )
        if not batch:
            return 0
        # Live workers are tracked by cancel/pause maps.
        live_ids = [bid for bid in self._cancel.keys() if bid != batch_id]
        if not live_ids:
            return 0
        older_live = []
        for bid in live_ids:
            other = self.db.fetch_one(
                "SELECT id,created_at,status FROM registration_batches WHERE id=?",
                (bid,),
            )
            if not other:
                continue
            if other["status"] in {"paused", "completed", "partial", "failed", "cancelled", "retried", "interrupted"}:
                continue
            if (
                other["created_at"] < batch["created_at"]
                or (other["created_at"] == batch["created_at"] and other["id"] < batch_id)
            ):
                older_live.append(bid)
        if not older_live:
            return 0
        placeholders = ",".join("?" for _ in older_live)
        row = self.db.fetch_one(
            f"""
            SELECT COUNT(*) total
            FROM registration_jobs
            WHERE status='queued' AND batch_id IN ({placeholders})
            """,
            tuple(older_live),
        ) or {}
        return int(row.get("total") or 0)

    def _acquire_account_slot(self, batch_id: str, cancel: threading.Event, pause: threading.Event) -> bool:
        with self._slot_cond:
            while True:
                if cancel.is_set() or pause.is_set():
                    return False
                limit = self.global_concurrency()
                free = limit - self._active_account_slots
                if free <= 0:
                    self._slot_cond.wait(timeout=0.2)
                    continue
                # Reserve free slots for older batches' remaining queued jobs first.
                older_queued = self._older_queued_demand(batch_id)
                if free > older_queued:
                    self._active_account_slots += 1
                    return True
                self._slot_cond.wait(timeout=0.2)

    def _release_account_slot(self) -> None:
        with self._slot_cond:
            self._active_account_slots = max(0, self._active_account_slots - 1)
            self._slot_cond.notify_all()
        # Free capacity may unlock older waiting batches that had no worker.
        try:
            self._kick_waiting_registrations()
        except Exception:
            pass


    def start(self, count: int | None, concurrency: int | None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = self.settings.registration_config()
        cfg.update(overrides or {})
        if count is None:
            raise ValueError("请设置注册数量")
        if concurrency is None:
            raise ValueError("请设置并发数")
        count = max(1, min(int(count), 25000))
        concurrency = max(
            1,
            min(
                int(concurrency),
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
        # Update global registration slots from start page concurrency.
        self.set_global_concurrency(concurrency)
        public_cfg = {
            **public_cfg,
            "slot_concurrency": concurrency,
        }
        with self._lock:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO registration_batches(id,status,target_count,concurrency,config_json) VALUES(?,?,?,?,?)",
                    (batch_id, "queued", count, concurrency, json.dumps(public_cfg, ensure_ascii=False)),
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
            self._cancel[batch_id] = cancel
            self._pause[batch_id] = pause
        self.events.publish(
            batch_id,
            f"[*] 注册批次已创建 · 数量 {count} · 全局槽位 {self.global_concurrency()}",
        )
        # Warm Camoufox + prefetch N one-shot tokens (N = concurrency).
        threading.Thread(
            target=_notify_local_solver,
            args=(cfg, concurrency),
            daemon=True,
            name=f"solver-warm-{batch_id[-6:]}",
        ).start()
        threading.Thread(
            target=self._run_batch,
            args=(batch_id, cfg, concurrency, cancel, pause),
            daemon=True,
            name=f"registration-{batch_id[-8:]}",
        ).start()
        # New batch may free/rebalance capacity; start any stranded waiting workers.
        self._kick_waiting_registrations()
        return self.get_batch(batch_id) or {"id": batch_id}

    def stop(self, batch_id: str) -> bool:
        with self._lock:
            batch = self.db.fetch_one("SELECT * FROM registration_batches WHERE id=?", (batch_id,))
            if not batch or batch["status"] not in {"queued", "running", "stopping", "pausing", "paused", "waiting"}:
                return False
            if batch["status"] in {"paused", "waiting"}:
                self.db.execute(
                    "UPDATE registration_jobs SET status='cancelled',stage='cancelled',message='任务已取消',updated_at=CURRENT_TIMESTAMP WHERE batch_id=? AND status IN ('queued','interrupted')",
                    (batch_id,),
                )
                self.db.execute(
                    "UPDATE registration_batches SET cancel_requested=1,pause_requested=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (batch_id,),
                )
                self._refresh_batch_counts(batch_id, final=True)
                self._cancel.pop(batch_id, None)
                self._pause.pop(batch_id, None)
                return True
            self.db.execute(
                "UPDATE registration_batches SET cancel_requested=1,pause_requested=0,status='stopping',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (batch_id,),
            )
            event = self._cancel.get(batch_id)
            pause = self._pause.get(batch_id)
        if pause:
            pause.clear()
        if event:
            event.set()
        self.events.publish(batch_id, "[!] 正在停止注册批次", "warning")
        return True

    def pause(self, batch_id: str) -> bool:
        with self._lock:
            batch = self.db.fetch_one("SELECT status FROM registration_batches WHERE id=?", (batch_id,))
            if not batch or batch["status"] not in {"queued", "running", "waiting"}:
                return False
            # Waiting batches have no active worker: pause immediately.
            if batch["status"] == "waiting":
                self.db.execute(
                    "UPDATE registration_batches SET pause_requested=1,status='paused',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (batch_id,),
                )
                self.db.execute(
                    "UPDATE registration_jobs SET status='interrupted',stage='queued',message='已暂停，等待继续',updated_at=CURRENT_TIMESTAMP WHERE batch_id=? AND status='queued'",
                    (batch_id,),
                )
                self.events.publish(batch_id, "[!] 排队中的注册批次已暂停", "warning")
                return True
            self.db.execute(
                "UPDATE registration_batches SET pause_requested=1,status='pausing',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (batch_id,),
            )
            pause = self._pause.get(batch_id)
            if pause:
                pause.set()
        self.events.publish(batch_id, "[!] 正在暂停注册批次，等待当前账号处理完成", "warning")
        return True

    def resume(self, batch_id: str) -> bool:
        """Resume a paused/waiting registration batch under shared global slots.

        Always (re)start a worker for this batch. Slot priority keeps older
        live batches filled first; free capacity is used by younger ones.
        """
        with self._lock:
            batch = self.get_batch(batch_id)
            if not batch:
                return False
            if batch["status"] not in {"paused", "waiting", "interrupted"}:
                return False
            # Remaining jobs may be interrupted after pause/restart — restore first.
            queued = self._restore_resumable_jobs(batch_id)
            if queued <= 0:
                self.db.execute(
                    "UPDATE registration_batches SET cancel_requested=0,pause_requested=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (batch_id,),
                )
                self._refresh_batch_counts(batch_id, final=True, paused=False)
                return False
            batch = self.get_batch(batch_id) or batch
            # If a worker is already alive, just clear pause and let it continue.
            if batch_id in self._cancel and batch_id in self._pause:
                self.db.execute(
                    "UPDATE registration_batches SET cancel_requested=0,pause_requested=0,status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (batch_id,),
                )
                self._pause[batch_id].clear()
                self._cancel[batch_id].clear()
                with self._slot_cond:
                    self._slot_cond.notify_all()
                self.events.publish(batch_id, "[*] 注册批次已继续（复用现有 worker）")
                return True
            return self._resume_now_locked(batch_id, batch)

    def _resume_now_locked(self, batch_id: str, batch: dict[str, Any] | None = None) -> bool:
        batch = batch or self.get_batch(batch_id)
        if not batch:
            return False
        if batch_id in self._cancel:
            # Worker already running — do not spawn a second one.
            self.db.execute(
                "UPDATE registration_batches SET cancel_requested=0,pause_requested=0,status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (batch_id,),
            )
            if batch_id in self._pause:
                self._pause[batch_id].clear()
            with self._slot_cond:
                self._slot_cond.notify_all()
            return True
        queued = self._restore_resumable_jobs(batch_id)
        if queued <= 0 and not any(job.get("status") == "queued" for job in (batch.get("jobs") or [])):
            return False
        batch = self.get_batch(batch_id) or batch
        cfg = self.settings.registration_config()
        cfg.update(batch.get("config") or {})
        # Pool size is per-batch dispatch cap; actual concurrency is global slots.
        concurrency = max(1, min(int(batch.get("concurrency") or self.global_concurrency()), runtime.registration_max_concurrency, self.global_concurrency()))
        cancel = threading.Event()
        pause = threading.Event()
        self._cancel[batch_id] = cancel
        self._pause[batch_id] = pause
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
        threading.Thread(
            target=_notify_local_solver,
            args=(cfg, concurrency),
            daemon=True,
            name=f"solver-warm-{batch_id[-6:]}",
        ).start()
        self.events.publish(batch_id, f"[*] 注册批次已继续 · 剩余 {queued} 个账号")
        threading.Thread(
            target=_notify_local_solver,
            args=(cfg, concurrency),
            daemon=True,
            name=f"solver-warm-{batch_id[-6:]}",
        ).start()
        return True

    def _restore_resumable_jobs(self, batch_id: str) -> int:
        """Turn unfinished interrupted jobs back to queued so resume can continue."""
        self.db.execute(
            """
            UPDATE registration_jobs
            SET status='queued', stage='queued', message='等待继续', error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE batch_id=? AND status='interrupted'
            """,
            (batch_id,),
        )
        row = self.db.fetch_one(
            "SELECT COUNT(*) total FROM registration_jobs WHERE batch_id=? AND status='queued'",
            (batch_id,),
        ) or {}
        return int(row.get('total') or 0)

    def _active_registration_ids(self, exclude: str | None = None) -> list[str]:
        rows = self.db.fetch_all(
            "SELECT id FROM registration_batches WHERE status IN ('queued','running','stopping','pausing') ORDER BY created_at ASC"
        )
        ids = [str(row["id"]) for row in rows]
        if exclude:
            ids = [i for i in ids if i != exclude]
        return ids

    def _start_next_waiting_registration(self) -> None:
        """Compatibility wrapper — prefer multi-batch kick under shared slots."""
        self._kick_waiting_registrations()

    def _kick_waiting_registrations(self) -> None:
        """Start workers for waiting batches that still have unfinished jobs.

        Shared-slot model: multiple batches may have workers. Waiting batches
        without a live worker must be started so they can consume free slots
        (after older live batches are filled first).
        """
        with self._lock:
            waiting = self.db.fetch_all(
                """
                SELECT id, status FROM registration_batches
                WHERE status IN ('waiting','interrupted')
                ORDER BY created_at ASC, id ASC
                """
            )
            for row in waiting:
                batch_id = str(row["id"])
                if batch_id in self._cancel:
                    continue  # already has a worker
                batch = self.get_batch(batch_id)
                if not batch:
                    continue
                queued = self._restore_resumable_jobs(batch_id)
                if queued <= 0:
                    self._refresh_batch_counts(batch_id, final=True, paused=False)
                    continue
                try:
                    self._resume_now_locked(batch_id, batch)
                except Exception as exc:
                    self.events.publish(batch_id, f"[-] 自动启动等待任务失败：{exc}", "error")

    def retry(self, batch_id: str) -> dict[str, Any]:
        """Retry failed jobs **in-place** on the same batch.

        Previously this created a brand-new batch (so the task list grew by one).
        Now we re-queue failed/cancelled/interrupted jobs on the same batch and
        start/resume its worker under the shared slot scheduler.
        """
        with self._lock:
            batch = self.get_batch(batch_id)
            if not batch:
                raise ValueError("batch not found")
            if batch["status"] in {"queued", "running", "stopping", "pausing", "waiting"}:
                raise ValueError("running batch cannot be retried")
            # Count retryable jobs
            retryable_ids = [
                str(job["id"])
                for job in (batch.get("jobs") or [])
                if job.get("status") in {"failed", "cancelled", "interrupted"}
            ]
            if not retryable_ids:
                raise ValueError("batch has no retryable jobs")

            # Reset jobs to queued on the same batch
            placeholders = ",".join("?" for _ in retryable_ids)
            self.db.execute(
                f"""
                UPDATE registration_jobs
                SET status='queued',
                    stage='queued',
                    message='等待重试',
                    error=NULL,
                    account_id=NULL,
                    email=COALESCE(email, email),
                    started_at=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE batch_id=? AND id IN ({placeholders})
                """,
                (batch_id, *retryable_ids),
            )
            # Clear terminal flags and put batch back into active queue
            self.db.execute(
                """
                UPDATE registration_batches
                SET cancel_requested=0,
                    pause_requested=0,
                    status='queued',
                    error=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (batch_id,),
            )
            self._refresh_batch_counts(batch_id, final=False, paused=False)
            self.events.publish(
                batch_id,
                f"[*] 已重试本任务失败账号 · {len(retryable_ids)} 个",
                "warning",
            )
            # Start worker if none alive
            batch = self.get_batch(batch_id) or batch
            started = self._resume_now_locked(batch_id, batch)
            if not started and batch_id not in self._cancel:
                # Fallback: force resume path
                cfg = self.settings.registration_config()
                cfg.update(batch.get("config") or {})
                concurrency = max(
                    1,
                    min(
                        int(batch.get("concurrency") or self.global_concurrency()),
                        runtime.registration_max_concurrency,
                        self.global_concurrency(),
                    ),
                )
                cancel = threading.Event()
                pause = threading.Event()
                self._cancel[batch_id] = cancel
                self._pause[batch_id] = pause
                threading.Thread(
                    target=self._run_batch,
                    args=(batch_id, cfg, concurrency, cancel, pause),
                    daemon=True,
                    name=f"registration-{batch_id[-8:]}",
                ).start()
                threading.Thread(
                    target=_notify_local_solver,
                    args=(cfg, concurrency),
                    daemon=True,
                    name=f"solver-warm-{batch_id[-6:]}",
                ).start()
            self._kick_waiting_registrations()
            return self.get_batch(batch_id) or {"id": batch_id}


    def close(self) -> None:
        with self._lock:
            for cancel in self._cancel.values():
                cancel.set()
            for pause in self._pause.values():
                pause.clear()

    def list_batches(self, limit: int = 100, offset: int = 0, q: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        query = (q or "").strip()
        if query:
            like = f"%{query}%"
            rows = self.db.fetch_all(
                "SELECT id,status,target_count,concurrency,completed,success,failed,cancel_requested,pause_requested,error,created_at,updated_at FROM registration_batches WHERE id LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (like, limit, offset),
            )
        else:
            rows = self.db.fetch_all(
                "SELECT id,status,target_count,concurrency,completed,success,failed,cancel_requested,pause_requested,error,created_at,updated_at FROM registration_batches ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        return [self._with_duration_stats(row) for row in rows]

    def _with_duration_stats(self, batch: dict[str, Any]) -> dict[str, Any]:
        """Attach elapsed/avg account duration for UI."""
        batch_id = str(batch.get("id") or "")
        if not batch_id:
            return batch
        created = str(batch.get("created_at") or "")
        updated = str(batch.get("updated_at") or created)
        terminal = str(batch.get("status") or "") in {
            "completed", "partial", "failed", "cancelled", "retried",
        }
        elapsed = self._seconds_between(created, updated if terminal else None)
        avg_row = self.db.fetch_one(
            """
            SELECT
              AVG(
                (julianday(updated_at) - julianday(COALESCE(started_at, created_at))) * 86400
              ) AS avg_sec,
              COUNT(*) AS finished
            FROM registration_jobs
            WHERE batch_id=? AND status IN ('success','failed','cancelled')
              AND COALESCE(started_at, created_at) IS NOT NULL
              AND updated_at IS NOT NULL
            """,
            (batch_id,),
        ) or {}
        avg_sec = avg_row.get("avg_sec")
        # Fallback avg: total elapsed / completed when per-job timestamps missing
        completed = int(batch.get("completed") or 0)
        if avg_sec is None and completed > 0 and elapsed > 0:
            avg_sec = elapsed / completed
        batch["elapsed_seconds"] = int(elapsed or 0)
        batch["avg_account_seconds"] = int(round(float(avg_sec))) if avg_sec is not None else None
        batch["finished_accounts"] = int(avg_row.get("finished") or 0)
        return batch

    @staticmethod
    def _seconds_between(start: str, end: str | None = None) -> int:
        from datetime import datetime, timezone
        def parse(value: str) -> datetime | None:
            raw = (value or "").strip()
            if not raw:
                return None
            if raw.endswith("Z"):
                raw = raw[:-1]
            raw = raw.replace("T", " ")
            try:
                dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
            return dt.replace(tzinfo=timezone.utc)
        a = parse(start)
        if not a:
            return 0
        if end:
            b = parse(end) or datetime.now(timezone.utc)
        else:
            b = datetime.now(timezone.utc)
        return max(0, int((b - a).total_seconds()))

    def count_batches(self, q: str | None = None) -> int:
        query = (q or "").strip()
        if query:
            row = self.db.fetch_one(
                "SELECT COUNT(*) total FROM registration_batches WHERE id LIKE ?",
                (f"%{query}%",),
            )
        else:
            row = self.db.fetch_one("SELECT COUNT(*) total FROM registration_batches")
        return int((row or {}).get("total") or 0)

    def clear_finished_batches(self) -> dict[str, int]:
        terminal = ("completed", "partial", "failed", "cancelled", "interrupted", "retried")
        placeholders = ",".join("?" for _ in terminal)
        with self.db.transaction() as conn:
            rows = conn.execute(
                f"SELECT id FROM registration_batches WHERE status IN ({placeholders})",
                terminal,
            ).fetchall()
            ids = [str(row[0]) for row in rows]
            if not ids:
                return {"batches": 0, "logs": 0}
            id_ph = ",".join("?" for _ in ids)
            logs = int(conn.execute(
                f"SELECT COUNT(*) FROM job_logs WHERE stream_id IN ({id_ph})",
                ids,
            ).fetchone()[0])
            conn.execute(f"DELETE FROM job_logs WHERE stream_id IN ({id_ph})", ids)
            conn.execute(f"DELETE FROM registration_jobs WHERE batch_id IN ({id_ph})", ids)
            conn.execute(f"DELETE FROM registration_batches WHERE id IN ({id_ph})", ids)
        return {"batches": len(ids), "logs": logs}

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
        return self._with_duration_stats(batch)

    def _run_batch(
        self,
        batch_id: str,
        cfg: dict[str, Any],
        concurrency: int,
        cancel: threading.Event,
        pause: threading.Event,
    ) -> None:
        self._sync_controls(batch_id, cancel, pause)
        initial = (
            "stopping" if cancel.is_set()
            else "pausing" if pause.is_set()
            else "waiting"  # becomes running only after at least one account job starts
        )
        self.db.execute(
            "UPDATE registration_batches SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (initial, batch_id),
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
                    while len(pending) < concurrency and not exhausted:
                        # Hold the same lock as pause()/stop() so no job can be dispatched
                        # after their durable control request is committed.
                        with self._lock:
                            if not self._sync_controls(batch_id, cancel, pause):
                                break
                            try:
                                job = next(cursor)
                            except StopIteration:
                                exhausted = True
                                break
                            pending[pool.submit(self._run_job, batch_id, job, cfg, cancel, pause)] = job
                        if cancel.is_set() or pause.is_set():
                            break
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
                    self._sync_controls(batch_id, cancel, pause)
                if cancel.is_set():
                    self.db.execute(
                        "UPDATE registration_jobs SET status='cancelled',stage='cancelled',message='任务开始前已取消',updated_at=CURRENT_TIMESTAMP WHERE batch_id=? AND status='queued'",
                        (batch_id,),
                    )
        finally:
            with self._lock:
                self._sync_controls(batch_id, cancel, pause)
                # Pause is durable: unfinished work must stay resumable.
                # Do NOT require status='queued' only — jobs may already be interrupted.
                if pause.is_set() and not cancel.is_set():
                    self.db.execute(
                        "UPDATE registration_jobs SET status='interrupted',stage='queued',message='已暂停，等待继续',updated_at=CURRENT_TIMESTAMP WHERE batch_id=? AND status='queued'",
                        (batch_id,),
                    )
                    unfinished = self.db.fetch_one(
                        "SELECT COUNT(*) total FROM registration_jobs WHERE batch_id=? AND status IN ('queued','interrupted','running')",
                        (batch_id,),
                    ) or {}
                    paused = int(unfinished.get("total") or 0) > 0
                else:
                    paused = False
                self._refresh_batch_counts(batch_id, final=not paused, paused=paused)
                if not paused:
                    # Kick next waiting batch only when this one truly finished.
                    self._start_next_waiting_registration()
            if not paused:
                with self._lock:
                    if self._cancel.get(batch_id) is cancel:
                        self._cancel.pop(batch_id, None)
                    if self._pause.get(batch_id) is pause:
                        self._pause.pop(batch_id, None)

    def _run_job(
        self,
        batch_id: str,
        job: dict[str, Any],
        cfg: dict[str, Any],
        cancel: threading.Event,
        pause: threading.Event,
    ) -> None:
        job_id = job["id"]
        slot = int(job["slot"])
        # Global shared registration slots across batches.
        if not self._acquire_account_slot(batch_id, cancel, pause):
            return
        try:
            with self._lock:
                if not self._sync_controls(batch_id, cancel, pause) or pause.is_set():
                    return
                self.db.execute(
                    "UPDATE registration_jobs SET status='running',stage='mailbox',message='正在启动注册任务',started_at=COALESCE(started_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (job_id,),
                )
                self.db.execute(
                    "UPDATE registration_batches SET status=CASE WHEN cancel_requested=1 THEN 'stopping' WHEN pause_requested=1 THEN 'pausing' ELSE 'running' END, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','waiting','running')",
                    (batch_id,),
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
        finally:
            self._release_account_slot()


    def _refresh_batch_counts(self, batch_id: str, final: bool = False, paused: bool = False) -> None:
        # Keep status aggregation serialized with pause()/stop() so a stale count
        # refresh cannot overwrite a just-committed pausing state.
        with self._lock:
            counts = self.db.fetch_one(
                "SELECT COUNT(*) completed, SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) success, SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed, SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) cancelled FROM registration_jobs WHERE batch_id=? AND status IN ('success','failed','cancelled')",
                (batch_id,),
            ) or {}
            batch = self.db.fetch_one("SELECT * FROM registration_batches WHERE id=?", (batch_id,)) or {}
            completed = int(counts.get("completed") or 0)
            success = int(counts.get("success") or 0)
            failed = int(counts.get("failed") or 0)
            unfinished = self.db.fetch_one(
                "SELECT COUNT(*) total FROM registration_jobs WHERE batch_id=? AND status IN ('queued','interrupted','running')",
                (batch_id,),
            ) or {}
            unfinished_total = int(unfinished.get("total") or 0)
            if paused:
                status = "paused"
            elif final:
                status = "cancelled" if batch.get("cancel_requested") else "completed"
                if success == 0 and failed:
                    status = "failed"
                elif failed:
                    status = "partial"
                # Safety: unfinished work must never finalize as completed.
                if unfinished_total > 0 and not batch.get("cancel_requested"):
                    status = "paused"
                    self.db.execute(
                        "UPDATE registration_batches SET pause_requested=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (batch_id,),
                    )
                    self.db.execute(
                        "UPDATE registration_jobs SET status='interrupted',stage='queued',message='已暂停，等待继续',updated_at=CURRENT_TIMESTAMP WHERE batch_id=? AND status='queued'",
                        (batch_id,),
                    )
            else:
                if batch.get("cancel_requested"):
                    status = "stopping"
                elif batch.get("pause_requested"):
                    status = "pausing"
                else:
                    running_jobs = self.db.fetch_one(
                        "SELECT COUNT(*) total FROM registration_jobs WHERE batch_id=? AND status='running'",
                        (batch_id,),
                    ) or {}
                    queued_jobs = self.db.fetch_one(
                        "SELECT COUNT(*) total FROM registration_jobs WHERE batch_id=? AND status='queued'",
                        (batch_id,),
                    ) or {}
                    # UX + scheduling truth:
                    # - running  = currently executing at least one account
                    # - waiting  = has unfinished work but no account running (slot-starved or not yet dispatched)
                    # A live worker waiting on global slots must show as waiting, not running.
                    if int(running_jobs.get("total") or 0) > 0:
                        status = "running"
                    elif int(queued_jobs.get("total") or 0) > 0:
                        status = "waiting"
                    else:
                        status = "running"
            self.db.execute(
                "UPDATE registration_batches SET status=?,completed=?,success=?,failed=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, completed, success, failed, batch_id),
            )
            if final and status in {"completed", "partial", "failed", "cancelled"}:
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
            elif paused and status == "paused":
                self.events.publish(
                    batch_id,
                    f"[!] 注册批次已暂停 · 已处理 {completed} · 成功 {success} · 失败 {failed} · 剩余可继续",
                    "warning",
                )

    def _sync_controls(self, batch_id: str, cancel: threading.Event, pause: threading.Event) -> bool:
        """Mirror durable control flags into this run's Events and allow dispatch."""
        batch = self.db.fetch_one(
            "SELECT cancel_requested,pause_requested FROM registration_batches WHERE id=?",
            (batch_id,),
        ) or {}
        if batch.get("cancel_requested"):
            cancel.set()
            pause.clear()
            return False
        if batch.get("pause_requested"):
            pause.set()
            return False
        pause.clear()
        return not cancel.is_set()
