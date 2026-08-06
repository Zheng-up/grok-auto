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


    def query(
        self,
        *,
        after_id: int = 0,
        limit: int = 200,
        q: str | None = None,
        level: str | None = None,
        kind: str | None = None,
        stream_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filtered log read for the full-page task log viewer.

        kind: all|batch|operation (or batches/operations aliases)
        level: info|success|warning|error (also matches message markers)
        stream_id: exact stream; empty = all matching kind
        after_id: 0 => latest N ascending; >0 => rows with id>after
        """
        limit = max(1, min(int(limit or 200), 1000))
        after_id = max(0, int(after_id or 0))
        kind_n = str(kind or "all").strip().lower()
        if kind_n in {"batches", "registration", "reg"}:
            kind_n = "batch"
        elif kind_n in {"operations", "ops", "op"}:
            kind_n = "operation"
        elif kind_n not in {"all", "batch", "operation"}:
            kind_n = "all"
        level_n = str(level or "").strip().lower()
        if level_n in {"", "all", "*"}:
            level_n = ""
        stream = str(stream_id or "").strip()
        needle = str(q or "").strip()
        if len(needle) > 200:
            needle = needle[:200]

        where: list[str] = []
        params: list[Any] = []
        if stream:
            where.append("stream_id = ?")
            params.append(stream)
        elif kind_n == "batch":
            where.append("stream_id LIKE 'batch_%'")
        elif kind_n == "operation":
            where.append("stream_id LIKE 'op_%'")

        if level_n == "success":
            where.append("(level = 'success' OR message LIKE '%[+]%')")
        elif level_n == "warning":
            where.append("(level = 'warning' OR message LIKE '%[!]%')")
        elif level_n == "error":
            where.append("(level = 'error' OR message LIKE '%[-]%')")
        elif level_n == "info":
            where.append(
                "( (level IS NULL OR level = '' OR level = 'info') "
                "AND message NOT LIKE '%[+]%' AND message NOT LIKE '%[!]%' AND message NOT LIKE '%[-]%' )"
            )

        if needle:
            # Search account email / account id / job id / batch|op stream id.
            # Also resolve accounts table so typing an email or acc_ id still hits
            # related registration/operation log lines even if wording differs.
            like = f"%{needle}%"
            clauses = ["message LIKE ?", "stream_id LIKE ?"]
            extra_params: list[Any] = [like, like]
            needle_l = needle.lower()
            try:
                # Direct account hits → include email + account id variants in OR.
                acc_rows = self.db.fetch_all(
                    """
                    SELECT id, email, source_job_id
                    FROM accounts
                    WHERE email LIKE ?
                       OR id LIKE ?
                       OR source_job_id LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT 20
                    """,
                    (like, like, like),
                )
            except Exception:
                acc_rows = []
            seen_tokens: set[str] = set()
            for row in acc_rows or []:
                for token in (
                    str(row.get("email") or "").strip(),
                    str(row.get("id") or "").strip(),
                    str(row.get("source_job_id") or "").strip(),
                ):
                    if not token or token in seen_tokens:
                        continue
                    seen_tokens.add(token)
                    clauses.append("message LIKE ?")
                    extra_params.append(f"%{token}%")
                    if token.startswith(("batch_", "op_", "job_")):
                        clauses.append("stream_id LIKE ?")
                        extra_params.append(f"%{token}%")
            # Bare numeric account slot search: "#12" or "账号 #12" / "账号#12"
            if needle.isdigit():
                clauses.append("message LIKE ?")
                extra_params.append(f"%账号 #{needle} %")
                clauses.append("message LIKE ?")
                extra_params.append(f"%账号 #{needle}·%")
                clauses.append("message LIKE ?")
                extra_params.append(f"%#{needle} ·%")
            elif needle_l.startswith("#") and needle_l[1:].isdigit():
                num = needle_l[1:]
                clauses.append("message LIKE ?")
                extra_params.append(f"%账号 #{num} %")
                clauses.append("message LIKE ?")
                extra_params.append(f"%#{num} %")
            where.append("(" + " OR ".join(clauses) + ")")
            params.extend(extra_params)

        if after_id > 0:
            where.append("id > ?")
            params.append(after_id)

        where_sql = " AND ".join(where) if where else "1=1"
        if after_id == 0:
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
        return self.db.fetch_all(
            f"""
            SELECT id,stream_id,level,message,created_at
            FROM job_logs
            WHERE {where_sql}
            ORDER BY id
            LIMIT ?
            """,
            (*params, limit),
        )

    def list_streams(self, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Recent streams that still have logs, for filter dropdowns."""
        limit = max(1, min(int(limit or 50), 200))
        kind_n = str(kind or "all").strip().lower()
        if kind_n in {"batches", "registration", "reg"}:
            kind_n = "batch"
        elif kind_n in {"operations", "ops", "op"}:
            kind_n = "operation"
        if kind_n == "batch":
            where = "stream_id LIKE 'batch_%'"
        elif kind_n == "operation":
            where = "stream_id LIKE 'op_%'"
        else:
            where = "1=1"
        return self.db.fetch_all(
            f"""
            SELECT stream_id,
                   COUNT(*) AS log_count,
                   MAX(id) AS last_id,
                   MAX(created_at) AS last_at
            FROM job_logs
            WHERE {where}
            GROUP BY stream_id
            ORDER BY last_id DESC
            LIMIT ?
            """,
            (limit,),
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
