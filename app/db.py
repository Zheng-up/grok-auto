from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    is_secret INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS registration_batches (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    target_count INTEGER NOT NULL,
    concurrency INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    pause_requested INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS registration_jobs (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES registration_batches(id) ON DELETE CASCADE,
    slot INTEGER NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'queued',
    message TEXT NOT NULL DEFAULT '',
    email TEXT,
    account_id TEXT,
    error TEXT,
    started_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id, slot)
);
CREATE INDEX IF NOT EXISTS idx_registration_jobs_batch ON registration_jobs(batch_id, slot);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_enc TEXT NOT NULL,
    sso_enc TEXT NOT NULL,
    oidc_enc TEXT,
    register_status TEXT NOT NULL DEFAULT 'success',
    oidc_status TEXT NOT NULL DEFAULT 'pending',
    remote_status TEXT NOT NULL DEFAULT 'not_pushed',
    remote_web_status TEXT NOT NULL DEFAULT 'not_pushed',
    remote_build_status TEXT NOT NULL DEFAULT 'not_pushed',
    remote_console_status TEXT NOT NULL DEFAULT 'not_pushed',
    cpa_file TEXT,
    source_job_id TEXT,
    last_error TEXT,
    oidc_error TEXT,
    remote_error TEXT,
    remote_web_error TEXT,
    remote_build_error TEXT,
    remote_console_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_accounts_created ON accounts(created_at DESC);
CREATE TABLE IF NOT EXISTS operation_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    total INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    pause_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS operation_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL REFERENCES operation_jobs(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    UNIQUE(operation_id, account_id)
);
CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_job_logs_stream ON job_logs(stream_id, id);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def initialize(self) -> None:
        with self._write_lock, self.connect() as conn:
            conn.executescript(SCHEMA)
            registration_job_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(registration_jobs)").fetchall()
            }
            if "started_at" not in registration_job_columns:
                conn.execute("ALTER TABLE registration_jobs ADD COLUMN started_at TEXT")
            registration_batch_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(registration_batches)").fetchall()
            }
            if "pause_requested" not in registration_batch_columns:
                conn.execute(
                    "ALTER TABLE registration_batches ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0"
                )
            account_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()
            }
            account_migrations = {
                "oidc_error": "TEXT",
                "remote_error": "TEXT",
                "remote_web_status": "TEXT NOT NULL DEFAULT 'not_pushed'",
                "remote_build_status": "TEXT NOT NULL DEFAULT 'not_pushed'",
                "remote_console_status": "TEXT NOT NULL DEFAULT 'not_pushed'",
                "remote_web_error": "TEXT",
                "remote_build_error": "TEXT",
                "remote_console_error": "TEXT",
            }
            for column, definition in account_migrations.items():
                if column not in account_columns:
                    conn.execute(f"ALTER TABLE accounts ADD COLUMN {column} {definition}")
            conn.execute(
                """
                UPDATE accounts SET remote_build_status='success'
                WHERE EXISTS (
                    SELECT 1 FROM operation_items item
                    JOIN operation_jobs job ON job.id=item.operation_id
                    WHERE item.account_id=accounts.id AND item.status='success' AND job.kind='remote_cpa'
                )
                """
            )
            conn.execute(
                """
                UPDATE accounts SET remote_web_status='success'
                WHERE EXISTS (
                    SELECT 1 FROM operation_items item
                    JOIN operation_jobs job ON job.id=item.operation_id
                    WHERE item.account_id=accounts.id AND item.status='success' AND job.kind IN ('remote_sso','remote_web')
                )
                """
            )
            conn.execute(
                """
                UPDATE accounts SET remote_build_status='failed'
                WHERE remote_build_status!='success' AND EXISTS (
                    SELECT 1 FROM operation_items item
                    JOIN operation_jobs job ON job.id=item.operation_id
                    WHERE item.account_id=accounts.id AND item.status='failed' AND job.kind='remote_cpa'
                )
                """
            )
            conn.execute(
                """
                UPDATE accounts SET remote_web_status='failed'
                WHERE remote_web_status!='success' AND EXISTS (
                    SELECT 1 FROM operation_items item
                    JOIN operation_jobs job ON job.id=item.operation_id
                    WHERE item.account_id=accounts.id AND item.status='failed' AND job.kind IN ('remote_sso','remote_web')
                )
                """
            )
            operation_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(operation_jobs)").fetchall()
            }
            if "cancel_requested" not in operation_columns:
                conn.execute(
                    "ALTER TABLE operation_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
                )
            if "pause_requested" not in operation_columns:
                conn.execute(
                    "ALTER TABLE operation_jobs ADD COLUMN pause_requested INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                "UPDATE registration_batches SET status='interrupted', error=COALESCE(error, 'service restarted'), updated_at=CURRENT_TIMESTAMP WHERE status IN ('queued','running','waiting','stopping','pausing')"
            )
            conn.execute(
                "UPDATE registration_jobs SET status='interrupted', error=COALESCE(error, 'service restarted'), updated_at=CURRENT_TIMESTAMP WHERE status IN ('queued','running')"
            )
            conn.execute(
                "UPDATE operation_jobs SET status='interrupted', error=COALESCE(error, 'service restarted'), updated_at=CURRENT_TIMESTAMP WHERE status IN ('queued','running','waiting','stopping','pausing')"
            )
            conn.execute(
                "UPDATE operation_items SET status='interrupted',message=CASE WHEN message='' THEN 'service restarted' ELSE message END WHERE status IN ('queued','running','waiting') AND operation_id IN (SELECT id FROM operation_jobs WHERE status='interrupted')"
            )
            interrupted_account_statuses = (
                ("oidc_status", "oidc_error", ("oidc",)),
                ("remote_web_status", "remote_web_error", ("remote_sso", "remote_web")),
                ("remote_build_status", "remote_build_error", ("remote_cpa",)),
                ("remote_console_status", "remote_console_error", ("remote_console",)),
            )
            for status_field, error_field, operation_kinds in interrupted_account_statuses:
                kind_placeholders = ",".join("?" for _ in operation_kinds)
                conn.execute(
                    f"""
                    UPDATE accounts
                    SET {status_field}='interrupted',
                        {error_field}=COALESCE(NULLIF({error_field}, ''), 'service restarted'),
                        updated_at=CURRENT_TIMESTAMP
                    WHERE {status_field} IN ('running','waiting') AND EXISTS (
                        SELECT 1 FROM operation_items item
                        JOIN operation_jobs job ON job.id=item.operation_id
                        WHERE item.account_id=accounts.id
                          AND item.status='interrupted'
                          AND job.status='interrupted'
                          AND job.kind IN ({kind_placeholders})
                    )
                    """,
                    operation_kinds,
                )
            equivalent_operation_kinds = (
                ("oidc",),
                ("remote_sso", "remote_web"),
                ("remote_cpa",),
                ("remote_console",),
            )
            for operation_kinds in equivalent_operation_kinds:
                kind_placeholders = ",".join("?" for _ in operation_kinds)
                conn.execute(
                    f"""
                    UPDATE operation_items AS old_item
                    SET status='resolved',
                        message=CASE
                            WHEN message='' THEN '后续同类操作已成功'
                            ELSE message || '；后续同类操作已成功'
                        END
                    WHERE old_item.status IN ('failed','interrupted')
                      AND EXISTS (
                        SELECT 1
                        FROM operation_jobs old_job
                        WHERE old_job.id=old_item.operation_id
                          AND old_job.kind IN ({kind_placeholders})
                      )
                      AND EXISTS (
                        SELECT 1
                        FROM operation_items new_item
                        JOIN operation_jobs new_job ON new_job.id=new_item.operation_id
                        WHERE new_item.account_id=old_item.account_id
                          AND new_item.id>old_item.id
                          AND new_item.status='success'
                          AND new_job.kind IN ({kind_placeholders})
                      )
                    """,
                    (*operation_kinds, *operation_kinds),
                )
            conn.execute(
                """
                UPDATE operation_jobs
                SET completed=(SELECT COUNT(*) FROM operation_items item WHERE item.operation_id=operation_jobs.id AND item.status IN ('success','resolved','failed','interrupted')),
                    success=(SELECT COUNT(*) FROM operation_items item WHERE item.operation_id=operation_jobs.id AND item.status IN ('success','resolved')),
                    failed=(SELECT COUNT(*) FROM operation_items item WHERE item.operation_id=operation_jobs.id AND item.status IN ('failed','interrupted')),
                    status=CASE
                        WHEN operation_jobs.status IN ('queued','running','waiting','stopping','pausing','paused') THEN operation_jobs.status
                        WHEN NOT EXISTS (SELECT 1 FROM operation_items item WHERE item.operation_id=operation_jobs.id AND item.status IN ('failed','interrupted')) THEN 'resolved'
                        WHEN operation_jobs.status='retried' THEN 'retried'
                        WHEN EXISTS (SELECT 1 FROM operation_items item WHERE item.operation_id=operation_jobs.id AND item.status IN ('success','resolved')) THEN 'partial'
                        ELSE 'failed'
                    END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE EXISTS (
                    SELECT 1 FROM operation_items item
                    WHERE item.operation_id=operation_jobs.id AND item.status='resolved'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO job_logs(stream_id,level,message)
                SELECT job.id,'success','[+] 历史失败项已由后续同类操作成功恢复'
                FROM operation_jobs job
                WHERE EXISTS (
                    SELECT 1 FROM operation_items item
                    WHERE item.operation_id=job.id AND item.status='resolved'
                ) AND NOT EXISTS (
                    SELECT 1 FROM job_logs log
                    WHERE log.stream_id=job.id AND log.message='[+] 历史失败项已由后续同类操作成功恢复'
                )
                """
            )

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._write_lock, self.connect() as conn:
            cursor = conn.execute(sql, tuple(params))
            return int(cursor.lastrowid or cursor.rowcount or 0)

    def executemany(self, sql: str, params: Iterable[Iterable[Any]]) -> None:
        with self._write_lock, self.connect() as conn:
            conn.executemany(sql, [tuple(row) for row in params])

    def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self.connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()