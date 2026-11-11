"""Redaction helpers for anything written to disk or printed.

Removes: authorization headers/signatures, API keys, cookies, nonces, and personal
data (names, user ids, e-mails, avatar links) contained in Onshape user-summary
objects. Geometry and feature data are preserved.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "<REDACTED>"

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "on-nonce",
    "x-api-key",
    "api-key",
    "openai-organization",
    "openai-project",
    "proxy-authorization",
}

# Keys whose values are personal data or credentials wherever they appear.
_SENSITIVE_KEYS = {
    "email",
    "image",
    "accesskey",
    "secretkey",
    "access_key",
    "secret_key",
    "api_key",
    "apikey",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "connectionname",
    "connectionnames",
}

# Objects describing a person: their identifying fields are redacted.
_PERSON_KEYS = {"owner", "createdby", "modifiedby", "creator", "lastmodifier", "user", "createdbyuser"}
_PERSON_FIELDS = {"name", "id", "href", "email", "image", "firstname", "lastname"}

_SECRET_PATTERNS = [
    re.compile(r"On [A-Za-z0-9]+:HmacSHA256:[A-Za-z0-9+/=]+"),  # Onshape signature header
    re.compile(r"Basic [A-Za-z0-9+/=]{8,}"),
    re.compile(r"Bearer [A-Za-z0-9._\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),  # OpenAI-style keys
]


def redact_text(text: str, extra_secrets: list[str] | None = None) -> str:
    """Remove known secret patterns and any explicitly supplied secret values."""
    out = text
    for s in extra_secrets or []:
        if s and len(s) >= 4:
            out = out.replace(s, REDACTED)
    for pat in _SECRET_PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: (REDACTED if k.lower() in SENSITIVE_HEADERS else v) for k, v in headers.items()}


def _is_person_summary(obj: dict) -> bool:
    jt = str(obj.get("jsonType", "")).lower()
    return jt in ("user-summary", "user-summary-info", "company-summary") or (
        "name" in obj and "href" in obj and "/users/" in str(obj.get("href", ""))
    )


def redact_json(obj: Any, _parent_key: str = "") -> Any:
    """Return a deep copy of obj with credentials and personal data removed."""
    if isinstance(obj, dict):
        person = _parent_key.lower() in _PERSON_KEYS or _is_person_summary(obj)
        out = {}
        for k, v in obj.items():
            kl = k.lower()
            if kl in _SENSITIVE_KEYS:
                out[k] = REDACTED if v not in (None, "", [], {}) else v
            elif person and kl in _PERSON_FIELDS:
                out[k] = REDACTED if v not in (None, "") else v
            else:
                out[k] = redact_json(v, k)
        return out
    if isinstance(obj, list):
        return [redact_json(x, _parent_key) for x in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
