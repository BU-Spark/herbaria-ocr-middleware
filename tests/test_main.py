"""HTTP-level tests for main.py.

Audit low 13: tests/ previously contained one module which imported only
`confidence`, so nothing exercised the endpoints. Every behaviour below could
break while the suite stayed green -- including the two security properties
main.py's own comments assert (the image URL is passed as an encoded query
param, and upstream exception text is never returned to the caller).

Run: python -m pytest tests/ -q
"""
import os
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as m  # noqa: E402
from confidence import SCHEMA_VERSION  # noqa: E402

client = TestClient(m.app)
IMG = "https://example.invalid/specimen.jpg"


# --------------------------------------------------------------------------- root
def test_root_reports_endpoints():
    r = client.get("/")
    assert r.status_code == 200
    assert "/evaluate/azure" in r.json()["Endpoints"]


# --------------------------------------------------------------------------- mock
def test_mock_missing_fixture_is_404():
    r = client.post("/evaluate/mock/999", params={"url": IMG})
    assert r.status_code == 404


def test_mock_without_url_is_422():
    # `url` is a required query param; FastAPI validates before the handler runs.
    r = client.post("/evaluate/mock/1")
    assert r.status_code == 422


def test_mock_returns_a_full_envelope():
    r = client.post("/evaluate/mock/1", params={"url": IMG})
    assert r.status_code == 200
    body = r.json()
    assert "_confidence" in body and isinstance(body["_confidence"], dict)
    assert body["_meta"] == {"model": "mock", "schema_version": SCHEMA_VERSION}
    # The flat DWC fields must survive alongside the envelope keys.
    assert [k for k in body if not k.startswith("_")], "no DWC fields returned"


# -------------------------------------------------------------------------- azure
class _Resp:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, status_code=200, payload=None, raise_on_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise = raise_on_json

    def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """Async context manager standing in for httpx.AsyncClient."""

    calls = []

    def __init__(self, response=None, exc=None, **kwargs):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, params=None, **kwargs):
        type(self).calls.append({"url": url, "params": params})
        if self._exc:
            raise self._exc
        return self._response


@pytest.fixture(autouse=True)
def _reset_calls():
    _FakeClient.calls = []
    yield


def _patch_upstream(monkeypatch, response=None, exc=None):
    monkeypatch.setattr(m.settings, "azure_route", "http://ocr.invalid/azure?key=SUPERSECRET")
    monkeypatch.setattr(
        m.httpx, "AsyncClient", lambda **kw: _FakeClient(response=response, exc=exc)
    )


def test_azure_passes_url_as_an_encoded_param_not_concatenated(monkeypatch):
    """Security property main.py asserts in a comment: the caller-supplied url is
    handed to httpx as a param, so it cannot smuggle extra query params into a
    route that already carries its own (e.g. an API key)."""
    _patch_upstream(monkeypatch, response=_Resp(payload={"scientificName": "Acer rubrum"}))
    hostile = "https://example.invalid/a.jpg&key=ATTACKER&extra=1"
    r = client.post("/evaluate/azure", params={"url": hostile})
    assert r.status_code == 200
    call = _FakeClient.calls[-1]
    assert call["params"] == {"url": hostile}, "url must travel as a param"
    assert "ATTACKER" not in call["url"], "caller input must not reach the route string"
    assert call["url"] == "http://ocr.invalid/azure?key=SUPERSECRET"


def test_azure_upstream_unreachable_is_502_and_hides_the_route(monkeypatch):
    """The other asserted property: the exception text can embed the upstream URL
    (which may hold a key), so it must never be returned to the caller."""
    _patch_upstream(monkeypatch, exc=httpx.ConnectError("cannot reach http://ocr.invalid/azure?key=SUPERSECRET"))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.status_code == 502
    assert "SUPERSECRET" not in r.text
    assert "ocr.invalid" not in r.text


def test_azure_upstream_non_200_is_propagated(monkeypatch):
    _patch_upstream(monkeypatch, response=_Resp(status_code=429, payload={}))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.status_code == 429
    assert "SUPERSECRET" not in r.text


def test_azure_upstream_non_json_200_is_502(monkeypatch):
    _patch_upstream(monkeypatch, response=_Resp(status_code=200, raise_on_json=True))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.status_code == 502


def test_azure_flat_dwc_becomes_an_envelope(monkeypatch):
    _patch_upstream(monkeypatch, response=_Resp(payload={"scientificName": "Acer rubrum"}))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.status_code == 200
    body = r.json()
    assert body["scientificName"] == "Acer rubrum"
    assert body["_meta"] == {"model": "azure", "schema_version": SCHEMA_VERSION}


# ------------------------------------------- audit medium 20: unrecognised shapes
@pytest.mark.parametrize(
    "payload,label",
    [
        # prebuilt-read / prebuilt-layout: analyzeResult with pages, no documents.
        ({"status": "succeeded",
          "analyzeResult": {"apiVersion": "2023-07-31",
                            "pages": [{"pageNumber": 1, "words": []}]}},
         "prebuilt-read"),
        # An analyze operation that has not finished yet.
        ({"status": "running", "createdDateTime": "2026-08-25T00:00:00Z"}, "in-flight"),
        # An upstream error object.
        ({"error": {"code": "InvalidRequest", "message": "bad image"}}, "error object"),
    ],
)
def test_azure_unrecognised_payload_is_502_not_a_fake_success(monkeypatch, payload, label):
    """Previously these fell through to the flat path, so every top-level key became
    a "DWC field": the portal received 200, matched nothing, and reported success
    while filling in nothing. A wrong answer that looks right is worse than an error."""
    _patch_upstream(monkeypatch, response=_Resp(payload=payload))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.status_code == 502, f"{label} should not be reported as success"
    assert "Unrecognised OCR payload shape" in r.json()["detail"]


def test_azure_empty_result_is_still_a_success(monkeypatch):
    """"OCR found nothing" is a legitimate outcome and must stay distinguishable
    from "we could not read the response"."""
    _patch_upstream(monkeypatch, response=_Resp(payload={}))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.status_code == 200
    assert r.json()["_confidence"] == {}


# ------------------------------------------------ audit low 14: _meta cannot be forged
def test_upstream_cannot_forge_meta(monkeypatch):
    _patch_upstream(monkeypatch, response=_Resp(payload={
        "scientificName": "Acer rubrum",
        "_confidence": {"scientificName": 0.9},
        "_meta": {"model": "totally-made-up", "schema_version": "99; DROP"},
    }))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.status_code == 200
    meta = r.json()["_meta"]
    assert meta["model"] == "azure", "an unknown model claim must fall back to the route's"
    assert meta["schema_version"] == SCHEMA_VERSION
    assert isinstance(meta["schema_version"], int) and not isinstance(meta["schema_version"], bool)


def test_upstream_may_still_declare_a_known_model(monkeypatch):
    """CONFIDENCE_CONTRACT.md allows "azure" or "mock"; that passthrough is
    intentional and covered by tests/test_confidence.py -- assert it over HTTP too."""
    _patch_upstream(monkeypatch, response=_Resp(payload={
        "scientificName": "Acer rubrum",
        "_confidence": {},
        "_meta": {"model": "mock", "schema_version": SCHEMA_VERSION},
    }))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.json()["_meta"]["model"] == "mock"


# ------------------------------------- audit low 12: missing config != upstream outage
def test_missing_azure_route_is_503_not_a_misleading_502(monkeypatch):
    """With AZURE_ROUTE unset, httpx failed on the empty default route and the broad
    `except Exception` reported 502 "Upstream OCR service unavailable" -- pointing the
    operator at a service that was never contacted. A deployment error should say so."""
    monkeypatch.setattr(m.settings, "azure_route", "")
    called = []
    monkeypatch.setattr(m.httpx, "AsyncClient",
                        lambda **kw: called.append(1) or _FakeClient(response=_Resp(payload={})))
    r = client.post("/evaluate/azure", params={"url": IMG})
    assert r.status_code == 503, "missing config must not masquerade as an upstream outage"
    assert "AZURE_ROUTE" in r.json()["detail"]
    assert not called, "no upstream call should be attempted with no route configured"
