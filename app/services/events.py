from __future__ import annotations

import threading
import time
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

    def read(self, stream_id: str, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT id,stream_id,level,message,created_at FROM job_logs WHERE stream_id=? AND id>? ORDER BY id LIMIT ?",
            (stream_id, max(after_id, 0), max(1, min(limit, 1000))),
        )

    def clear(self, stream_id: str) -> int:
        return self.db.execute("DELETE FROM job_logs WHERE stream_id=?", (stream_id,))

    def clear_all(self) -> dict[str, int]:
        active_batches = self.db.fetch_one(
            "SELECT COUNT(*) total FROM registration_batches WHERE status IN ('queued','running','stopping','pausing','paused')"
        ) or {}
        active_operations = self.db.fetch_one(
            "SELECT COUNT(*) total FROM operation_jobs WHERE status IN ('queued','running','waiting','stopping','pausing','paused')"
        ) or {}
        if int(active_batches.get("total") or 0) or int(active_operations.get("total") or 0):
            raise ValueError("running tasks must finish or stop before clearing history")
        with self.db.transaction() as conn:
            logs = int(conn.execute("SELECT COUNT(*) FROM job_logs").fetchone()[0])
            batches = int(conn.execute("SELECT COUNT(*) FROM registration_batches").fetchone()[0])
            operations = int(conn.execute("SELECT COUNT(*) FROM operation_jobs").fetchone()[0])
            conn.execute("DELETE FROM job_logs")
            conn.execute("DELETE FROM registration_batches")
            conn.execute("DELETE FROM operation_jobs")
        return {"logs": logs, "batches": batches, "operations": operations}

    def wait(self, timeout: float = 10.0) -> None:
        with self._condition:
            self._condition.wait(timeout=max(0.1, timeout))

    def prune(self, days: int = 30) -> None:
        self.db.execute(
            "DELETE FROM job_logs WHERE created_at < datetime('now', ?)",
            (f"-{max(1, days)} days",),
        )