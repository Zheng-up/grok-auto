from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import Database

PBKDF2_ITERATIONS = 600_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("password must contain at least 8 characters")
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(derived).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_b64, hash_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


class AuthService:
    def __init__(self, db: Database, session_hours: int = 24):
        self.db = db
        self.session_hours = session_hours

    def is_initialized(self) -> bool:
        return bool(self.db.fetch_one("SELECT id FROM users LIMIT 1"))

    def initialize_admin(self, username: str, password: str) -> None:
        username = username.strip()
        if len(username) < 3:
            raise ValueError("username must contain at least 3 characters")
        password_hash = hash_password(password)
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("administrator already initialized")
            conn.execute(
                "INSERT INTO users(username,password_hash) VALUES(?,?)",
                (username, password_hash),
            )

    def login(self, username: str, password: str) -> tuple[str, datetime]:
        row = self.db.fetch_one("SELECT * FROM users WHERE username=?", (username.strip(),))
        if not row or not verify_password(password, row["password_hash"]):
            raise ValueError("invalid username or password")
        token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = _utcnow() + timedelta(hours=self.session_hours)
        self.db.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (_utcnow().isoformat(),))
        self.db.execute(
            "INSERT INTO admin_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
            (token_hash, row["id"], expires.isoformat()),
        )
        return token, expires

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return self.db.fetch_one(
            "SELECT u.id,u.username,s.expires_at FROM admin_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?",
            (token_hash, _utcnow().isoformat()),
        )

    def logout(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM admin_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))