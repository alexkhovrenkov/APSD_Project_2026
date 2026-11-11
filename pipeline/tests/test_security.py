"""Offline checks: request signing, credential redaction, redirect safety, retries.
Uses fake HTTP sessions and dummy keys; nothing is sent to Onshape."""

import base64
import hashlib
import hmac
import json

import pytest

from apsd_pipeline import onshape_client as oc
from apsd_pipeline.config import ConfigError, OnshapeCredentials, get_openai_settings, parse_document_url
from apsd_pipeline.onshape_client import OnshapeAPIError, OnshapeClient, sign_request
from apsd_pipeline.redact import REDACTED, redact_headers, redact_json, redact_text

AK, SK = "DUMMYACCESSKEY1234", "dummy-secret-key-5678"
TARGET = parse_document_url("https://cad.onshape.com/documents/6877e7a6059a24d360e8c6e1/w/008a5bb3f73e5b75f4629e14/e/a93d3af214699fcc1c182757")


def test_signature_matches_documented_construction():
    url = "https://cad.onshape.com/api/v17/documents/abc?x=1&Y=2"
    nonce, date = "AbCdEfGhIjKlMnOpQrStUvWxY", "Mon, 11 Apr 2016 20:08:56 GMT"
    header = sign_request("GET", url, AK, SK, nonce, date)
    # independent reconstruction of the docs' sample: (method\nnonce\ndate\nctype\npath\nquery\n).lower()
    s = ("GET\n" + nonce + "\n" + date + "\napplication/json\n/api/v17/documents/abc\nx=1&Y=2\n").lower()
    expected = base64.b64encode(hmac.new(SK.encode(), s.encode(), hashlib.sha256).digest()).decode()
    assert header == f"On {AK}:HmacSHA256:{expected}"


def test_redaction_of_headers_text_and_json():
    h = redact_headers({"Authorization": "On X:HmacSHA256:abc", "On-Nonce": "n", "Cookie": "c", "Accept": "a"})
    assert h == {"Authorization": REDACTED, "On-Nonce": REDACTED, "Cookie": REDACTED, "Accept": "a"}
    t = redact_text(f"auth On {AK}:HmacSHA256:Zm9vYmFy= and Basic QUJDREVGR0g= key sk-abcdefghijklmnop1234", [SK])
    assert AK not in t and "Zm9vYmFy" not in t and "QUJDREVGR0g" not in t and "sk-abcdef" not in t
    doc = {"owner": {"name": "Jane Student", "id": "u1", "href": "https://cad.onshape.com/api/users/u1"},
           "createdBy": {"jsonType": "user-summary", "name": "Jane Student", "id": "u1"},
           "features": [{"name": "Revolve 1", "expression": "5 mm"}], "email": "jane@example.org"}
    r = redact_json(doc)
    s = json.dumps(r)
    assert "Jane" not in s and "jane@" not in s and "u1" not in s
    assert r["features"][0] == {"name": "Revolve 1", "expression": "5 mm"}  # geometry/features preserved


def test_credentials_repr_hides_secrets():
    c = OnshapeCredentials("hmac", AK, SK)
    assert AK not in repr(c) and SK not in repr(c)


class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
        self.trust_env = True

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        assert allow_redirects is False
        self.calls.append((url, dict(headers)))
        return self.responses.pop(0)


def _client(responses, mode="hmac"):
    s = FakeSession(responses)
    return OnshapeClient(TARGET, OnshapeCredentials(mode, AK, SK), session=s), s


def test_same_host_redirect_is_resigned():
    c, s = _client([FakeResp(307, headers={"Location": "https://cad.onshape.com/api/v17/other?a=1"}), FakeResp(200, {"ok": 1})])
    data, rec = c.get_json("x", "/documents/d")
    assert data == {"ok": 1} and len(s.calls) == 2
    (u1, h1), (u2, h2) = s.calls
    assert u2.endswith("/other?a=1") and h1["Authorization"] != h2["Authorization"]
    assert all(SK not in json.dumps(r.to_dict()) for r in c.log)
    assert "Authorization" not in json.dumps(rec.to_dict())


def test_cross_host_redirect_refused_without_forwarding_credentials():
    c, s = _client([FakeResp(307, headers={"Location": "https://evil.example.com/steal"})])
    with pytest.raises(OnshapeAPIError, match="different host"):
        c.get_json("x", "/documents/d")
    assert len(s.calls) == 1  # never contacted the other host


def test_429_retry_honours_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(oc.time, "sleep", slept.append)
    c, s = _client([FakeResp(429, {"m": "slow"}, {"Retry-After": "3"}), FakeResp(200, {"ok": 1})])
    assert c.get_json("x", "/documents/d")[0] == {"ok": 1}
    assert slept == [3.0] and c.log[0].attempts == 2


def test_retries_are_bounded(monkeypatch):
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)
    c, s = _client([FakeResp(503, {})] * oc.MAX_ATTEMPTS)
    with pytest.raises(OnshapeAPIError):
        c.get_json("x", "/documents/d")
    assert len(s.calls) == oc.MAX_ATTEMPTS


def test_error_messages_do_not_leak_secrets():
    c, _ = _client([FakeResp(401, {"message": f"bad key {AK} {SK}"})])
    with pytest.raises(OnshapeAPIError) as e:
        c.get_json("x", "/documents/d")
    assert AK not in str(e.value) and SK not in str(e.value)


def test_anonymous_mode_sends_no_authorization():
    c, s = _client([FakeResp(200, {})], mode="anonymous")
    c.get_json("x", "/documents/d")
    assert "Authorization" not in s.calls[0][1]


def test_only_get_is_possible():
    assert not any(hasattr(OnshapeClient, m) for m in ("post", "post_json", "delete", "put"))


@pytest.mark.parametrize("url", ["http://cad.onshape.com/documents/a/w/b/e/c", "https://evil.com/documents/" + "a" * 24 + "/w/" + "b" * 24 + "/e/" + "c" * 24,
                                 "https://cad.onshape.com/documents/xyz/w/b/e/c"])
def test_bad_urls_rejected(url):
    with pytest.raises(ConfigError):
        parse_document_url(url)


def test_missing_openai_settings_message_names_variables(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "")
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        get_openai_settings()
