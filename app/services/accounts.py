from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.config import CPA_AUTH_DIR
from app.crypto import SecretBox
from app.db import Database
from app.registration.models import RegisteredAccount


class AccountService:
    def __init__(self, db: Database, box: SecretBox):
        self.db = db
        self.box = box

    def save_registered(self, account: RegisteredAccount, source_job_id: str) -> str:
        existing = self.db.fetch_one("SELECT id FROM accounts WHERE email=?", (account.email,))
        account_id = existing["id"] if existing else f"acc_{uuid.uuid4().hex[:16]}"
        oidc = json.dumps(account.oauth, ensure_ascii=False) if account.oauth else ""
        self.db.execute(
            """
            INSERT INTO accounts(id,email,password_enc,sso_enc,oidc_enc,register_status,oidc_status,source_job_id,updated_at)
            VALUES(?,?,?,?,?,'success',?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(email) DO UPDATE SET
              password_enc=excluded.password_enc,sso_enc=excluded.sso_enc,
              oidc_enc=CASE WHEN excluded.oidc_enc='' THEN accounts.oidc_enc ELSE excluded.oidc_enc END,
              register_status='success',oidc_status=excluded.oidc_status,
              source_job_id=excluded.source_job_id,last_error=NULL,updated_at=CURRENT_TIMESTAMP
            """,
            (
                account_id,
                account.email,
                self.box.encrypt(account.password),
                self.box.encrypt(account.sso),
                self.box.encrypt(oidc) if oidc else "",
                "success" if account.oauth else "pending",
                source_job_id,
            ),
        )
        return account_id

    @staticmethod
    def _where(
        query: str,
        register_status: str = "",
        oidc_status: str = "",
        remote_web_status: str = "",
        remote_build_status: str = "",
        remote_console_status: str = "",
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if query.strip():
            clauses.append("a.email LIKE ?")
            params.append(f"%{query.strip()}%")
        filters = {
            "a.register_status": register_status,
            "a.oidc_status": oidc_status,
            "a.remote_web_status": remote_web_status,
            "a.remote_build_status": remote_build_status,
            "a.remote_console_status": remote_console_status,
        }
        for column, value in filters.items():
            if value.strip():
                clauses.append(f"{column}=?")
                params.append(value.strip().lower())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def list(
        self,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        register_status: str = "",
        oidc_status: str = "",
        remote_web_status: str = "",
        remote_build_status: str = "",
        remote_console_status: str = "",
    ) -> list[dict[str, Any]]:
        where, params = self._where(
            query,
            register_status,
            oidc_status,
            remote_web_status,
            remote_build_status,
            remote_console_status,
        )
        params.extend([max(1, min(limit, 2000)), max(offset, 0)])
        rows = self.db.fetch_all(
            f"""
            SELECT a.id,a.email,a.register_status,a.oidc_status,a.remote_status,
                   a.remote_web_status,a.remote_build_status,a.remote_console_status,a.cpa_file,
                   a.source_job_id,a.last_error,a.oidc_error,a.remote_error,
                   a.remote_web_error,a.remote_build_error,a.remote_console_error,
                   a.created_at,a.updated_at,
                   COALESCE((
                       SELECT GROUP_CONCAT(DISTINCT job.kind)
                       FROM operation_items item
                       JOIN operation_jobs job ON job.id=item.operation_id
                       WHERE item.account_id=a.id
                         AND job.status IN ('queued','running','waiting','stopping','pausing','paused')
                   ), '') active_operations_csv
            FROM accounts a {where}
            ORDER BY a.created_at DESC LIMIT ? OFFSET ?
            """,
            params,
        )
        for row in rows:
            active = str(row.pop("active_operations_csv") or "")
            row["active_operations"] = [kind for kind in active.split(",") if kind]
            row["has_sso"] = True
            row["has_oidc"] = row["oidc_status"] == "success"
        return rows

    def count(
        self,
        query: str = "",
        register_status: str = "",
        oidc_status: str = "",
        remote_web_status: str = "",
        remote_build_status: str = "",
        remote_console_status: str = "",
    ) -> int:
        where, params = self._where(
            query,
            register_status,
            oidc_status,
            remote_web_status,
            remote_build_status,
            remote_console_status,
        )
        row = self.db.fetch_one(f"SELECT COUNT(*) total FROM accounts a {where}", params)
        return int((row or {}).get("total") or 0)

    def get(self, account_id: str, *, reveal: bool = False) -> dict[str, Any] | None:
        row = self.db.fetch_one("SELECT * FROM accounts WHERE id=?", (account_id,))
        if not row:
            return None
        if reveal:
            row["password"] = self.box.decrypt(row.pop("password_enc"))
            row["sso"] = self.box.decrypt(row.pop("sso_enc"))
            oidc_raw = self.box.decrypt(row.pop("oidc_enc")) if row.get("oidc_enc") else ""
            row["oidc"] = json.loads(oidc_raw) if oidc_raw else None
        else:
            row.pop("password_enc", None)
            row.pop("sso_enc", None)
            row.pop("oidc_enc", None)
            row["has_sso"] = True
            row["has_oidc"] = row["oidc_status"] == "success"
        return row

    def selected(self, ids: list[str] | None = None, *, reveal: bool = False) -> list[dict[str, Any]]:
        if not ids:
            rows = self.db.fetch_all("SELECT id FROM accounts ORDER BY created_at DESC")
            ids = [row["id"] for row in rows]
        return [item for account_id in ids if (item := self.get(account_id, reveal=reveal))]

    def set_oidc(self, account_id: str, oidc: dict[str, Any], cpa_file: str) -> None:
        self.db.execute(
            "UPDATE accounts SET oidc_enc=?,oidc_status='success',cpa_file=?,oidc_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (self.box.encrypt(json.dumps(oidc, ensure_ascii=False)), cpa_file, account_id),
        )

    def set_status(self, account_id: str, field: str, status: str, error: str | None = None) -> None:
        error_field = {
            "oidc_status": "oidc_error",
            "remote_status": "remote_error",
            "remote_web_status": "remote_web_error",
            "remote_build_status": "remote_build_error",
            "remote_console_status": "remote_console_error",
        }.get(field)
        if not error_field:
            raise ValueError("invalid status field")
        self.db.execute(
            f"UPDATE accounts SET {field}=?,{error_field}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, error, account_id),
        )

    def delete(self, account_ids: list[str]) -> int:
        count = 0
        auth_root = CPA_AUTH_DIR.resolve()
        for account_id in dict.fromkeys(account_ids):
            row = self.db.fetch_one("SELECT cpa_file FROM accounts WHERE id=?", (account_id,))
            if not row:
                continue
            count += self.db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            path = Path(str(row.get("cpa_file") or "")).resolve()
            if path.parent == auth_root and path.is_file():
                path.unlink(missing_ok=True)
        return count