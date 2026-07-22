from __future__ import annotations

import threading
from typing import Any

from app.db import Database


class EventLog:
    def __init__(self, db: Database):
        self.db = db
        self._condition = threading.Condition()

    def publish(self, stream_id: str, message: str, level: str = "info") -> int:
        row_id = self.db.execute(
            "INSERT INTO job_logs(stream_id,level,message) VALUES(?,?,?)",
            (stream_id, level, str(message)[:4000]),
        )
        with self._condition:
            self._condition.notify_all()
        return row_id

    def _tail(self, where_sql: str, params: tuple[Any, ...], limit: int) -> list[dict[str, Any]]:
        # Latest N rows ascending by id for initial viewer bootstrap.
        return self.db.fetch_all(
            f"""
            SELECT id,stream_id,level,message,created_at FROM (
              SELECT id,stream_id,level,message,created_at
              FROM job_logs
              WHERE {where_sql}
              ORDER BY id DESC
              LIMIT ?
            ) ORDER BY id ASC
            """,
            (*params, limit),
        )

    def read(self, stream_id: str, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        after_id = max(after_id, 0)
        is_registration = stream_id == "registration"
        if after_id == 0:
            if is_registration:
                return self._tail("stream_id LIKE 'batch_%'", (), limit)
            return self._tail("stream_id=?", (stream_id,), limit)
        if is_registration:
            return self.db.fetch_all(
                """
                SELECT id,stream_id,level,message,created_at
                FROM job_logs
                WHERE stream_id LIKE 'batch_%' AND id>?
                ORDER BY id
                LIMIT ?
                """,
                (after_id, limit),
            )
        return self.db.fetch_all(
            "SELECT id,stream_id,level,message,created_at FROM job_logs WHERE stream_id=? AND id>? ORDER BY id LIMIT ?",
            (stream_id, after_id, limit),
        )

    def clear(self, stream_id: str) -> int:
        if stream_id == "registration":
            return self.db.execute("DELETE FROM job_logs WHERE stream_id LIKE 'batch_%'")
        return self.db.execute("DELETE FROM job_logs WHERE stream_id=?", (stream_id,))

    def clear_all(self) -> dict[str, int]:
        # Only clear logs. Never touch registration batches / operation jobs.
        with self.db.transaction() as conn:
            logs = int(conn.execute("SELECT COUNT(*) FROM job_logs").fetchone()[0])
            conn.execute("DELETE FROM job_logs")
        return {"logs": logs, "batches": 0, "operations": 0}

    def clear_all_tasks(self, registration, operations) -> dict[str, int]:
        # Clear finished tasks only; keep active/running/waiting/paused.
        reg = registration.clear_finished_batches()
        ops = operations.clear_finished()
        return {
            "batches": int(reg.get("batches") or 0),
            "operations": int(ops.get("operations") or 0),
            "logs": int(reg.get("logs") or 0) + int(ops.get("logs") or 0),
        }

    def wait(self, timeout: float = 10.0) -> None:
        with self._condition:
            self._condition.wait(timeout=max(0.1, timeout))

    def prune(self, days: int = 30) -> None:
        self.db.execute(
            "DELETE FROM job_logs WHERE created_at < datetime('now', ?)",
            (f"-{max(1, days)} days",),
        )
