# herbaria-ocr-server

A middleware service for running AI Optical Character Recognition (OCR) inferences on images from the herbaria portal. This is a FastAPI application that proxies to OCR backends (mock or Azure) and normalizes their responses.

## Architecture

This service is a **proxy and response normalizer** — no OCR model runs
in-process here. Each endpoint forwards to an upstream OCR provider, then
reshapes the reply into the flat DWC + `_confidence` envelope defined in
`CONFIDENCE_CONTRACT.md`:

- **Mock endpoint** (`/evaluate/mock/{id}`): reads canned sample responses
  from `test_data/` for testing without a live OCR call.
- **Azure endpoint** (`/evaluate/azure`): forwards to the upstream OCR service
  configured via `AZURE_ROUTE` (currently `BU-Spark/spark-symbiota-ml`) and
  normalizes its response.

This repo owns the portal-facing envelope, the mock route, confidence
sanitizing, and upstream-credential leak prevention; the ML repo owns the OCR
pipelines and their dependency tree.

### Integration with the Symbiota app

This middleware does not run in isolation — the main Symbiota web container calls
it over the shared Docker network. The Symbiota PHP integration
(`collections/quickentry/rpc/externalocr.php`) reaches the service at the
hardcoded URL `http://ocr_middleware:8000/...`.

> **Hostname note (service name).** The canonical service hostname is the
> **underscore** form `ocr_middleware` (port 8000) — used by the compose service
> name, the hardcoded PHP URL in `externalocr.php`, and now also by the main stack's
> `containers/.env.example` (`OCR_HOST=ocr_middleware`). Earlier the `.env.example`
> shipped a hyphenated `ocr-middleware` that would not resolve on the Docker network;
> that is fixed (finding C3). Always use the underscore form.

Because `externalocr.php` resolves the service by its container hostname rather
than via the published `8000:8000` port mapping, the web container must be on the
**same Docker network** as this service. The compose file places this service on
the external `symbiota-network`; the web container must also be attached to that
network so it can reach `http://ocr_middleware:8000/`. See the main containers
spine doc (`se-symbiota/containers/README.md`) for how the full stack is wired
together, including bridging this service onto the app's network.

## API Documentation

FastAPI interactive docs are accessible at `http://localhost:8000/docs` by default.

### Available Endpoints

- `GET /` - Service info and available endpoints
- `POST /evaluate/mock/{id}` - Mock evaluation (test data)
- `POST /evaluate/azure?url=...` - Azure OCR service

## Configuration

Configuration is managed via environment variables loaded from `.env` file. The service uses Pydantic's BaseSettings for automatic `.env` loading.

### Environment Variables

- `APP_NAME` - Service display name
- `DISPLAY_NAME` - API display name
- `SERVER_VERSION` - Server version string
- `API_VERSION` - API version string
- `HOST` - Bind host (default: `0.0.0.0`)
- `PORT` - Bind port (default: `8000`)
- `AZURE_ROUTE` - Azure OCR endpoint URL (optional)

### Setup Steps

1. Copy `.env.example` to `.env` and customize for your deployment:
   ```bash
   cp .env.example .env
   ```

   > This `.env` (at the `herbaria-ocr-middleware` repo root) configures the **Python
   > OCR service only**. It is a *separate file* from `se-symbiota/containers/.env`,
   > which configures the PHP/DB stack — don't confuse the two.

2. Edit `.env` with your settings:
   ```env
   APP_NAME=My OCR Service
   AZURE_ROUTE=https://your-azure-endpoint.com/ocr
   ```

3. Create the shared Docker network, then run Docker Compose:

   The compose file (`docker/docker-compose.yaml`) attaches this service to an
   **external** network named `symbiota-network`, so that network must already
   exist before you start the service. Create it once:
   ```bash
   docker network create symbiota-network
   ```

   The compose file lives in the `docker/` subdirectory, **not** the repo root.
   Running `docker compose up -d` from the repo root fails with
   `no configuration file provided: not found`. Either `cd docker` first, or
   point at the file explicitly:
   ```bash
   cd docker
   docker compose up -d
   # ...or from the repo root:
   # docker compose -f docker/docker-compose.yaml up -d
   ```

   > Note: the obsolete top-level `version:` key has been removed from this compose
   > file (finding C7), so modern Docker no longer prints a deprecation warning.

## Running the Middleware

### With Docker

Run from the `docker/` subdirectory (the compose file is not at the repo root),
and make sure the external `symbiota-network` exists first (see
[Setup Steps](#setup-steps)):

```bash
docker network create symbiota-network   # one-time, if it does not exist yet
cd docker
docker compose up -d
```

### Bridging onto the Symbiota app network

Starting the service as above puts it on the external `symbiota-network`, but
that is **not** the network the main Symbiota app stack runs on, so the web
container still cannot reach it yet. When the `containers/` stack comes up, its
Compose project creates a project-prefixed network named
**`containers_symbiota-network`** (the app compose declares the network with
`driver: bridge` rather than `external` + an explicit `name:`, so Compose
prepends the project name). This OCR service, by contrast, attaches to the
plain external **`symbiota-network`**. They are two distinct networks, so the
web container's lookup of `http://ocr_middleware:8000/` fails until the two are
bridged.

To make the running OCR container reachable from the web container, connect it
onto the app's network and give it the `ocr_middleware` alias (the hostname the
PHP integration hardcodes — see the [Integration with the Symbiota app](#integration-with-the-symbiota-app)
section and the hostname gotcha above):

```bash
docker network connect --alias ocr_middleware containers_symbiota-network <ocr container>
```

Replace `<ocr container>` with the running container's name (e.g.
`docker-ocr_middleware-1`); find it with `docker ps`. After this, the web
container resolves `http://ocr_middleware:8000/` over the shared network.

For the full end-to-end wiring of the three-container stack (app + DB + OCR),
including where this bridging step fits, see step 7 of the main containers
spine doc (`se-symbiota/containers/README.md`).

### Locally (Development)

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## Testing

Access FastAPI docs at: http://localhost:8000/docs

Try the mock endpoint. The `url` query parameter is **required** (the endpoint
`POST /evaluate/mock/{id}` declares `url: str = Query(...)` in `main.py`), so
omitting it returns `HTTP 422 Unprocessable Entity`. Pass any URL value:
```bash
curl -X POST "http://localhost:8000/evaluate/mock/1?url=http://example.com/x.jpg"
```

## Development Notes

- Configuration uses `pydantic_settings` with automatic `.env` loading
- All endpoints return JSON responses
- Async/await pattern throughout for efficient concurrency
