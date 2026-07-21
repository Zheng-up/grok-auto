from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key_path: str | Path):
        self.key_path = Path(key_path)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            self.key_path.write_bytes(Fernet.generate_key())
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
        self._fernet = Fernet(self.key_path.read_bytes().strip())

    def encrypt(self, value: str | None) -> str:
        if not value:
            return ""
        return self._fernet.encrypt(str(value).encode("utf-8")).decode("ascii")

    def decrypt(self, value: str | None) -> str:
        if not value:
            return ""
        try:
            return self._fernet.decrypt(str(value).encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise RuntimeError("encrypted value cannot be decrypted with current key") from exc