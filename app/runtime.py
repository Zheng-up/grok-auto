from __future__ import annotations

from app.auth import AuthService
from app.config import DB_PATH, SECRET_KEY_PATH, ensure_runtime_dirs, runtime
from app.crypto import SecretBox
from app.db import Database
from app.registration.runner import RegistrationRunner
from app.services.accounts import AccountService
from app.services.events import EventLog
from app.services.exports import ExportService
from app.services.oidc import OidcService
from app.services.operations import OperationManager
from app.services.remote import RemotePoolService
from app.services.settings import SettingsService


class ApplicationServices:
    def __init__(self) -> None:
        ensure_runtime_dirs()
        self.db = Database(DB_PATH)
        self.db.initialize()
        self.box = SecretBox(SECRET_KEY_PATH)
        self.auth = AuthService(self.db, runtime.session_hours)
        self.settings = SettingsService(self.db, self.box)
        self.accounts = AccountService(self.db, self.box)
        self.events = EventLog(self.db)
        self.exports = ExportService(self.accounts)
        self.oidc = OidcService(self.accounts, self.settings, self.events)
        self.remote = RemotePoolService(self.accounts, self.settings, self.events)
        self.operations = OperationManager(self.db, self.events, self.settings)
        self.operations.register("oidc", lambda account_id, stream: self.oidc.mint(account_id, stream))
        self.operations.register("remote_sso", lambda account_id, stream: self.remote.push(account_id, "web", stream), before=self.remote.wait_for_cooldown)
        self.operations.register("remote_web", lambda account_id, stream: self.remote.push(account_id, "web", stream), before=self.remote.wait_for_cooldown)
        self.operations.register("remote_cpa", lambda account_id, stream: self.remote.push(account_id, "build", stream), before=self.remote.wait_for_cooldown)
        self.operations.register("remote_console", lambda account_id, stream: self.remote.push(account_id, "console", stream), before=self.remote.wait_for_cooldown)
        self.oidc.on_minted = self._queue_auto_build
        self.registration = RegistrationRunner(
            self.db,
            self.settings,
            self.accounts,
            self.events,
            queue_operation=lambda kind, account_id: self.operations.queue_one(kind, account_id),
        )

    def _queue_auto_build(self, account_id: str) -> None:
        if bool(self.settings.get("remote_build_auto_push", False)):
            self.operations.queue_one("remote_cpa", account_id)

    def close(self) -> None:
        self.registration.close()
        self.operations.close()


services = ApplicationServices()