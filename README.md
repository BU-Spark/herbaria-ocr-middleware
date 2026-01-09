# herbaria-ocr-server

A middleware service for running AI Optical Character Recognition (OCR) inferences on images from the herbaria portal. This is a FastAPI application that can delegate to various OCR backends (mock, Azure, or local models).

## Architecture

This service acts as a unified interface for OCR operations:
- **Mock endpoint**: For testing with sample data
- **Azure endpoint**: Delegates to Azure OCR service  
- **Model endpoints**: Extensible framework for local ML models (from `/app/models`)

Models are discovered dynamically at startup from the mounted models directory, making it easy to add/update models without restarting.

## API Documentation

FastAPI interactive docs are accessible at `http://localhost:8000/docs` by default.

### Available Endpoints

- `GET /` - Service info and available models
- `GET /models` - List all available models
- `POST /evaluate/mock/{id}` - Mock evaluation (test data)
- `POST /evaluate/azure?url=...` - Azure OCR service
- `POST /evaluate/{model_name}?url=...` - Local model evaluation (future implementation)

## Configuration

Configuration is managed via environment variables loaded from `.env` file. The service uses Pydantic's BaseSettings for automatic `.env` loading.

### Environment Variables

- `APP_NAME` - Service display name
- `DISPLAY_NAME` - API display name
- `MODEL_NAME` - Default model name for identification
- `SERVER_VERSION` - Server version string
- `API_VERSION` - API version string
- `HOST` - Bind host (default: `0.0.0.0`)
- `PORT` - Bind port (default: `8000`)
- `MODEL_PATH` - Path to models directory (default: `/app/models`)
- `AZURE_ROUTE` - Azure OCR endpoint URL (optional)

### Setup Steps

1. Copy `.env.example` to `.env` and customize for your deployment:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your settings:
   ```env
   APP_NAME=My OCR Service
   MODEL_PATH=/app/models
   AZURE_ROUTE=https://your-azure-endpoint.com/ocr
   ```

3. Run with Docker Compose:
   ```bash
   docker compose up -d
   ```

## Model Deployment

### Directory Structure

Models should be organized in subdirectories under `MODEL_PATH`:

```
/app/models/
  ├── model-1/
  │   ├── model.pkl
  │   ├── config.json
  │   └── inference.py
  └── model-2/
      ├── model.pkl
      ├── config.json
      └── inference.py
```

### Adding Models

Models are discovered automatically at startup. Each model subdirectory is addressable via:
```
POST /evaluate/{model_name}?url=...
```

Example:
```bash
curl -X POST "http://localhost:8000/evaluate/model-1?url=https://example.com/image.jpg"
```

### Local Model Implementation

To implement actual model inference, modify the `/evaluate/{model_name}` endpoint in `main.py` to:
1. Load model from `available_models[model_name]["path"]`
2. Run inference on the image URL
3. Return results

## Running the Middleware

### With Docker

```bash
docker compose up -d
```

### Locally (Development)

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

## Testing

Access FastAPI docs at: http://localhost:8000/docs

Try the mock endpoint:
```bash
curl -X POST "http://localhost:8000/evaluate/mock/1"
```

## Development Notes

- Configuration uses `pydantic_settings` with automatic `.env` loading
- Models are discovered dynamically on startup by scanning `MODEL_PATH`
- All endpoints return JSON responses
- Async/await pattern throughout for efficient concurrency
