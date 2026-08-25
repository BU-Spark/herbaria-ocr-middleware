import os
import json
from fastapi import FastAPI, HTTPException, Query
from pydantic import ConfigDict
from pydantic_settings import BaseSettings
import aiofiles
import httpx

from confidence import UnrecognisedPayload, to_envelope, synthesize_confidence

class Settings(BaseSettings):
    """Configuration loaded from environment variables"""
    model_config = ConfigDict(
        env_file=".env",
        extra="ignore",
        protected_namespaces=()
    )

    app_name: str = "Symbiota OCR Middleware"
    display_name: str = "OCR Service"
    server_version: str = "1.0.0"
    api_version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8000
    azure_route: str = ""

settings = Settings()
app = FastAPI(title=settings.app_name)

@app.get("/")
def read_root():
    return {
        "Message": "Hello World. This message indicates that this server is up and running.",
        "Display name": settings.display_name,
        "Server version": settings.server_version,
        "API version": settings.api_version,
        "Endpoints": ["/evaluate/mock/{id}", "/evaluate/azure"]
    }

DATA_DIR = 'test_data'

@app.post("/evaluate/mock/{id}")
async def output(id: int, url: str = Query(...)):
    # Verify that the url is received
    print("Received URL:", url)

    # get filename
    filename = os.path.join(DATA_DIR, f"{id}.json")
    # Check if the JSON file exists; if not, raise a 404 error.
    if not os.path.exists(filename):
        raise HTTPException(status_code=404, detail="JSON file not found")

    # read json data
    async with aiofiles.open(filename, mode='r') as f:
        contents = await f.read()
        data = json.loads(contents)

    # Synthesize a plausible per-field confidence map so the front-end can be
    # developed against realistic data, then return the standard envelope.
    if isinstance(data, dict) and "_confidence" not in data:
        data = dict(data)
        data["_confidence"] = synthesize_confidence(data)
    try:
        return to_envelope(data, model="mock")
    except UnrecognisedPayload as e:
        # A fixture that is not a transcription. Surfacing it beats returning a 200
        # whose "fields" are really some other schema's keys.
        raise HTTPException(status_code=502, detail=f"Unrecognised OCR payload shape: {e}")

@app.post("/evaluate/azure")
async def evaluate(url: str = Query(...)):
    # Pass the image URL as a properly-encoded query param. Never string-concat
    # `?url=` onto azure_route: the route may itself carry query params (e.g. an
    # API key), and a raw caller-supplied url could smuggle extra params.
    # Distinguish "not configured" from "configured but unreachable". Both used to
    # arrive as 502 "Upstream OCR service unavailable" -- httpx would fail on the
    # empty default route and get swallowed by the except below, sending whoever was
    # debugging off to check a service that was never called. Compose supplies
    # AZURE_ROUTE via .env; if it is missing, that is a deployment error, not an
    # upstream outage.
    if not settings.azure_route:
        raise HTTPException(
            status_code=503,
            detail="AZURE_ROUTE is not configured; this deployment cannot reach an OCR service",
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:  # ponytail: 30s covers typical DI analyze latency; raise if models get slower
            response = await client.post(settings.azure_route, params={"url": url})
    except Exception as e:
        # Log server-side only; the exception text can embed the upstream URL
        # (which may contain a key), so never return it to the caller.
        print(f"Error calling OCR service: {e}")
        raise HTTPException(status_code=502, detail="Upstream OCR service unavailable")

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="OCR service returned an error")

    try:
        payload = response.json()
    except ValueError:
        # 200 with an empty/truncated body (proxy hiccup) — don't leak a 500.
        raise HTTPException(status_code=502, detail="OCR service returned a malformed response")
    try:
        return to_envelope(payload, model="azure")
    except UnrecognisedPayload as e:
        # The upstream answered 200 with JSON we cannot read as a transcription --
        # e.g. a prebuilt-read/layout result (analyzeResult.pages, no documents) or an
        # in-flight {"status": ...} operation. Previously this fell through to the flat
        # path, so every top-level key became a "DWC field": the portal got a 200,
        # matched nothing, and reported success while filling in nothing at all.
        print(f"Unrecognised OCR payload shape from upstream: {e}")
        raise HTTPException(status_code=502, detail=f"Unrecognised OCR payload shape: {e}")
