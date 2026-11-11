"""Minimal, read-only Onshape REST client.

Authentication follows https://onshape-public.github.io/docs/auth/apikeys/ :
  * Request signature (default): headers Date, On-Nonce, Content-Type and
    ``Authorization: On <AccessKey>:HmacSHA256:<Signature>`` where Signature is
    base64(HMAC-SHA256(secretKey, lower(method\\nnonce\\ndate\\ncontentType\\npath\\nquery\\n))).
  * Basic (docs: "for local testing only"): ``Authorization: Basic base64(access:secret)``.
  * Anonymous: no Authorization header (only works for link-shared documents and
    only for the endpoints Onshape exposes anonymously).

Safety properties:
  * Only GET is implemented -> the client cannot modify CAD, sharing or versions.
  * 307 redirects are re-signed for the new URL (as the docs require) and are only
    followed to the same stack host; credentials are never sent to another host.
  * Bounded retries: 429 (honours Retry-After up to a cap), 502/503/504, timeouts.
  * Nothing secret is logged; the request log stores method, sanitized URL,
    timestamp, status and a few non-sensitive response headers.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import string
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse

import requests

from .config import OnshapeCredentials, OnshapeTarget
from .redact import redact_text

API_VERSION = "v17"  # "Current" server in https://cad.onshape.com/api/openapi (checked 2026-11-09)
USER_AGENT = "APSD-LATHE-pipeline/1.0 (student coursework; read-only)"
CONTENT_TYPE = "application/json"
RETRY_STATUSES = {429, 502, 503, 504}
MAX_ATTEMPTS = 4
MAX_RETRY_AFTER_S = 120
TIMEOUT_S = (10, 60)  # connect, read
LOGGED_RESPONSE_HEADERS = ("Date", "X-Api-Version", "X-Rate-Limit-Remaining", "Retry-After", "X-Deprecated", "Content-Type")


class OnshapeAPIError(RuntimeError):
    def __init__(self, status: int | None, message: str, url: str):
        self.status = status
        self.url = url
        super().__init__(f"HTTP {status} for {url}: {message}")


def make_nonce(length: int = 25) -> str:
    """Alphanumeric nonce; docs require >=16 chars, unique per request."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def http_date(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.strftime("%a, %d %b %Y %H:%M:%S GMT")


def sign_request(method: str, url: str, access_key: str, secret_key: str, nonce: str, date: str,
                 content_type: str = CONTENT_TYPE) -> str:
    """Return the Authorization header value for an Onshape HMAC-signed request."""
    parsed = urlparse(url)
    string_to_sign = (
        f"{method}\n{nonce}\n{date}\n{content_type}\n{parsed.path}\n{parsed.query}\n"
    ).lower()
    digest = hmac.new(secret_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode("ascii")
    return f"On {access_key}:HmacSHA256:{signature}"


@dataclass
class RequestRecord:
    """One redacted line of API-request evidence."""

    name: str
    method: str
    url: str
    timestamp_utc: str
    status: int | None
    attempts: int
    auth_mode: str
    response_headers: dict[str, str] = field(default_factory=dict)
    saved_as: str | None = None
    sha256: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class OnshapeClient:
    def __init__(self, target: OnshapeTarget, creds: OnshapeCredentials, session: requests.Session | None = None):
        self.target = target
        self.creds = creds
        self.session = session or requests.Session()
        self.session.trust_env = True
        self.log: list[RequestRecord] = []

    # -- helpers -----------------------------------------------------------------
    def url(self, path: str, params: dict[str, Any] | None = None) -> str:
        q = urlencode({k: v for k, v in (params or {}).items() if v is not None}, doseq=True)
        return f"{self.target.host}/api/{API_VERSION}{path}" + (f"?{q}" if q else "")

    def _headers(self, url: str) -> dict[str, str]:
        h = {"Accept": "application/json;charset=UTF-8; qs=0.09", "User-Agent": USER_AGENT, "Content-Type": CONTENT_TYPE}
        if self.creds.mode == "hmac":
            nonce, date = make_nonce(), http_date()
            h.update({"Date": date, "On-Nonce": nonce,
                      "Authorization": sign_request("GET", url, self.creds.access_key, self.creds.secret_key, nonce, date)})
        elif self.creds.mode == "basic":
            token = base64.b64encode(f"{self.creds.access_key}:{self.creds.secret_key}".encode()).decode()
            h["Authorization"] = f"Basic {token}"
        return h

    def _sanitize(self, text: str) -> str:
        return redact_text(text, [self.creds.access_key, self.creds.secret_key])

    # -- public ------------------------------------------------------------------
    def get_json(self, name: str, path: str, params: dict[str, Any] | None = None) -> tuple[Any, RequestRecord]:
        """GET an endpoint and return (parsed JSON, request record). Raises OnshapeAPIError."""
        url = self.url(path, params)
        rec = RequestRecord(name=name, method="GET", url=url,
                            timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                            status=None, attempts=0, auth_mode=self.creds.mode)
        self.log.append(rec)
        redirects = 0
        current = url
        while True:
            rec.attempts += 1
            try:
                resp = self.session.get(current, headers=self._headers(current), timeout=TIMEOUT_S, allow_redirects=False)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if rec.attempts >= MAX_ATTEMPTS:
                    rec.error = self._sanitize(f"{type(exc).__name__}: network failure after {rec.attempts} attempts")
                    raise OnshapeAPIError(None, rec.error, url) from None
                time.sleep(min(2 ** rec.attempts, 20))
                continue

            rec.status = resp.status_code
            rec.response_headers = {k: resp.headers[k] for k in LOGGED_RESPONSE_HEADERS if k in resp.headers}

            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                nxt = urlparse(location if "://" in location else self.target.host + location)
                if (nxt.hostname or "").lower() != self.target.hostname or nxt.scheme != "https":
                    rec.error = f"refused redirect to a different host ({nxt.hostname}); credentials not forwarded"
                    raise OnshapeAPIError(resp.status_code, rec.error, url)
                redirects += 1
                if redirects > 3:
                    rec.error = "too many redirects"
                    raise OnshapeAPIError(resp.status_code, rec.error, url)
                current = nxt.geturl()  # re-signed on the next loop iteration
                rec.attempts -= 1
                continue

            if resp.status_code in RETRY_STATUSES and rec.attempts < MAX_ATTEMPTS:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after is not None else 2 ** rec.attempts
                except ValueError:
                    delay = 2 ** rec.attempts
                if delay > MAX_RETRY_AFTER_S:
                    rec.error = f"rate limited; Retry-After={retry_after}s exceeds cap {MAX_RETRY_AFTER_S}s"
                    raise OnshapeAPIError(resp.status_code, rec.error, url)
                time.sleep(delay)
                continue

            if resp.status_code != 200:
                msg = self._sanitize(resp.text[:300])
                hint = {
                    401: "not authenticated (missing/invalid keys, or endpoint not available anonymously)",
                    402: "annual API call limit reached for this account",
                    403: "authenticated but no permission for this document/endpoint",
                    404: "document/workspace/element not found on this stack",
                }.get(resp.status_code, "")
                rec.error = f"{hint} | body: {msg}".strip(" |")
                raise OnshapeAPIError(resp.status_code, rec.error, url)
            try:
                return resp.json(), rec
            except json.JSONDecodeError:
                rec.error = "response was not JSON"
                raise OnshapeAPIError(resp.status_code, rec.error, url) from None
