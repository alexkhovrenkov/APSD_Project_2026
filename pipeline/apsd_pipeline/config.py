"""Configuration: Onshape URL parsing and secure credential loading.

Secrets are read ONLY from environment variables (optionally loaded from a local,
git-ignored ``pipeline/.env``). They are never written to disk, printed or logged.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PIPELINE_DIR = Path(__file__).resolve().parent.parent

# Team target (non-secret): Part Studio tab "APSD-LATHE-Final" (team instruction 2026-11-11).
# Earlier runs used tab a93d3af214699fcc1c182757, now named "WIP". Overridable with ONSHAPE_DOCUMENT_URL.
DEFAULT_DOCUMENT_URL = (
    "https://cad.onshape.com/documents/6877e7a6059a24d360e8c6e1/"
    "w/008a5bb3f73e5b75f4629e14/e/147da5f19a2ac767328e5f7f"
)
# Drawing tab "APSD-LATHE-Final" in the same document. Overridable with ONSHAPE_DRAWING_ELEMENT_ID
# (set it to an empty string to skip the drawing).
DEFAULT_DRAWING_ELEMENT_ID = "39fec93b315c33208dd4a282"

# Hosts we are willing to send Onshape credentials to. Enterprise stacks look like
# https://<company>.onshape.com ; add yours via ONSHAPE_HOST if needed.
_ONSHAPE_HOST_RE = re.compile(r"^[a-z0-9-]+\.onshape\.com$")

_ID_RE = re.compile(r"^[0-9a-f]{24}$")


class ConfigError(RuntimeError):
    """Raised for missing/invalid configuration. Messages never contain secrets."""


@dataclass(frozen=True)
class OnshapeTarget:
    host: str  # e.g. https://cad.onshape.com
    document_id: str
    wvm: str  # "w", "v" or "m"
    wvm_id: str
    element_id: str

    @property
    def hostname(self) -> str:
        return urlparse(self.host).hostname or ""


def load_dotenv_if_present() -> None:
    """Load pipeline/.env into os.environ (existing variables win). No-op if absent."""
    env_file = PIPELINE_DIR / ".env"
    if env_file.exists():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)


def parse_document_url(url: str) -> OnshapeTarget:
    """Parse https://<stack>.onshape.com/documents/<did>/<w|v|m>/<id>/e/<eid>."""
    p = urlparse(url.strip())
    if p.scheme != "https":
        raise ConfigError("Onshape URL must use https://")
    host = (p.hostname or "").lower()
    if not _ONSHAPE_HOST_RE.match(host):
        raise ConfigError(f"Host '{host}' is not an onshape.com stack host")
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 6 or parts[0] != "documents" or parts[2] not in ("w", "v", "m") or parts[4] != "e":
        raise ConfigError("URL must look like /documents/<did>/<w|v|m>/<id>/e/<eid>")
    did, wvm, wvm_id, eid = parts[1], parts[2], parts[3], parts[5]
    for name, val in (("document", did), (wvm, wvm_id), ("element", eid)):
        if not _ID_RE.match(val):
            raise ConfigError(f"{name} id '{val}' is not a 24-character hex Onshape id")
    return OnshapeTarget(f"https://{host}", did, wvm, wvm_id, eid)


def get_drawing_element_id() -> str | None:
    val = os.environ.get("ONSHAPE_DRAWING_ELEMENT_ID")
    val = DEFAULT_DRAWING_ELEMENT_ID if val is None else val.strip()
    if not val:
        return None
    if not _ID_RE.match(val):
        raise ConfigError("ONSHAPE_DRAWING_ELEMENT_ID must be a 24-character hex Onshape element id")
    return val


def get_target() -> OnshapeTarget:
    url = os.environ.get("ONSHAPE_DOCUMENT_URL", "").strip() or DEFAULT_DOCUMENT_URL
    target = parse_document_url(url)
    host_override = os.environ.get("ONSHAPE_HOST", "").strip()
    if host_override:
        override = urlparse(host_override)
        if override.scheme != "https" or not _ONSHAPE_HOST_RE.match((override.hostname or "").lower()):
            raise ConfigError("ONSHAPE_HOST must be https://<stack>.onshape.com")
        if override.hostname.lower() != target.hostname:
            raise ConfigError(
                f"ONSHAPE_HOST ({override.hostname}) does not match the document URL host "
                f"({target.hostname}). API keys only work on the stack where they were created."
            )
    return target


@dataclass(frozen=True)
class OnshapeCredentials:
    mode: str  # "hmac", "basic" or "anonymous"
    access_key: str = ""
    secret_key: str = ""

    def __repr__(self) -> str:  # never show secrets, even in tracebacks/debuggers
        return f"OnshapeCredentials(mode={self.mode!r}, access_key=<redacted>, secret_key=<redacted>)"


def get_onshape_credentials(requested_mode: str | None = None) -> OnshapeCredentials:
    """Return credentials for the requested auth mode.

    hmac (default): signed requests with ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY.
    basic: Basic auth with the same key pair (Onshape docs: "for local testing only").
    anonymous: no credentials; works only for endpoints a link-shared document exposes.
    """
    mode = (requested_mode or os.environ.get("ONSHAPE_AUTH_MODE", "") or "hmac").strip().lower()
    if mode not in ("hmac", "basic", "anonymous"):
        raise ConfigError("ONSHAPE_AUTH_MODE must be one of: hmac, basic, anonymous")
    if mode == "anonymous":
        return OnshapeCredentials("anonymous")
    ak = os.environ.get("ONSHAPE_ACCESS_KEY", "").strip()
    sk = os.environ.get("ONSHAPE_SECRET_KEY", "").strip()
    if not ak or not sk:
        raise ConfigError(
            "ONSHAPE_ACCESS_KEY and/or ONSHAPE_SECRET_KEY are not set. Create an API key in "
            "Onshape (Account > Developer > API keys, read scope is enough), then put both values "
            "in pipeline/.env (see .env.example) or export them in your shell. "
            "Never paste keys into chat, code or commits."
        )
    return OnshapeCredentials(mode, ak, sk)


def get_openai_settings() -> tuple[str, str]:
    """Return (api_key, model). Raises ConfigError naming what is missing (never the values)."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "").strip()
    missing = [n for n, v in (("OPENAI_API_KEY", key), ("OPENAI_MODEL", model)) if not v]
    if missing:
        raise ConfigError(
            f"Missing {', '.join(missing)}. Set them in pipeline/.env or your environment "
            "(Colab: use the Secrets panel). The OpenAI key is separate from the Onshape keys."
        )
    return key, model
