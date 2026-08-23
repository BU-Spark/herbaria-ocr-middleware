"""Route tests covering changes introduced by PR #4 (remove model scaffold)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

client = TestClient(app)


# --- Root endpoint ------------------------------------------------------------

def test_root_lists_real_endpoints():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "/evaluate/mock/{id}" in body["Endpoints"]
    assert "/evaluate/azure" in body["Endpoints"]


def test_root_has_no_model_fields():
    """Root must not expose model-scaffold keys removed by PR #4."""
    body = client.get("/").json()
    assert "Available models" not in body
    assert "Model name" not in body


# --- /models is gone ---------------------------------------------------------

def test_models_endpoint_absent():
    """/models was deleted; the route must not exist."""
    r = client.get("/models")
    assert r.status_code == 404


# --- Generic /evaluate/{model_name} is gone ----------------------------------

@pytest.mark.parametrize("model_name", ["tensorflow", "custom-model"])
def test_generic_model_dispatch_absent(model_name):
    """POST /evaluate/<arbitrary-name> must 404 now that the catch-all route
    was removed.  The two concrete routes (/evaluate/mock/{id} and
    /evaluate/azure) must NOT be affected."""
    r = client.post(f"/evaluate/{model_name}?url=http://example.com/img.jpg")
    assert r.status_code == 404


# --- Surviving concrete routes still exist -----------------------------------

def test_mock_endpoint_still_reachable():
    """/evaluate/mock/{id} must still return a valid response for id=1."""
    r = client.post("/evaluate/mock/1?url=http://example.com/img.jpg")
    # The test_data/1.json fixture exists; expect 200 with envelope keys.
    assert r.status_code == 200
    body = r.json()
    assert "_confidence" in body
    assert "_meta" in body
    assert body["_meta"]["model"] == "mock"


def test_azure_endpoint_still_reachable():
    """The Azure POST route must remain registered without calling upstream."""
    assert any(
        route.path == "/evaluate/azure" and "POST" in route.methods
        for route in app.routes
    )
