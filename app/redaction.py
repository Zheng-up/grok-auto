from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import quote, quote_plus

_SENSITIVE_PAIR = re.compile(
    r"(?i)(app_key|api_key|access_token|refresh_token|id_token|authorization|password)=([^&\s]+)"
)
_BEARER = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIALS = re.compile(r"(https?://)([^/@\s:]+):([^/@\s]+)@", re.I)


def redact_error(error: object, secrets: Iterable[object] = ()) -> str:
    text = str(error or "unknown error")
    for value in secrets:
        secret = str(value or "")
        if len(secret) < 4:
            continue
        for encoded in {secret, quote(secret, safe=""), quote_plus(secret)}:
            text = text.replace(encoded, "***")
    text = _SENSITIVE_PAIR.sub(lambda match: f"{match.group(1)}=***", text)
    text = _BEARER.sub("Bearer ***", text)
    text = _URL_CREDENTIALS.sub(r"\1***:***@", text)
    return text[:2000]