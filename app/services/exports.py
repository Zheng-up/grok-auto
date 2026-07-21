from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.config import CPA_AUTH_DIR
from app.services.accounts import AccountService


class ExportService:
    def __init__(self, accounts: AccountService):
        self.accounts = accounts

    def tokens_txt(self, ids: list[str] | None = None) -> bytes:
        rows = self.accounts.selected(ids, reveal=True)
        return ("".join(f"{row['sso']}\n" for row in rows)).encode("utf-8")

    def accounts_txt(self, ids: list[str] | None = None) -> bytes:
        rows = self.accounts.selected(ids, reveal=True)
        return ("".join(
            f"{row['email']}----{row['password']}----{row['sso']}\n"
            for row in rows
        )).encode("utf-8")

    def cpa_zip(self, ids: list[str] | None = None) -> bytes:
        rows = self.accounts.selected(ids, reveal=False)
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest = []
            auth_root = CPA_AUTH_DIR.resolve()
            for row in rows:
                path = Path(str(row.get("cpa_file") or "")).resolve()
                if (
                    row.get("oidc_status") != "success"
                    or not path.is_file()
                    or path.parent != auth_root
                ):
                    continue
                archive.write(path, arcname=path.name)
                manifest.append({"account_id": row["id"], "email": row["email"], "file": path.name})
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return target.getvalue()