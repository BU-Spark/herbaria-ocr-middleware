import os
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from pydantic_settings import BaseSettings
import aiofiles
import httpx

class Settings(BaseSettings):
    """Configuration loaded from environment variables"""
    app_name: str = "Symbiota OCR Middleware"
    display_name: str = "OCR Service"
    model_name: str = "Default Model"
    server_version: str = "1.0.0"
    api_version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8000
    model_path: str = "/app/models"
    azure_route: str = ""

    class Config:
        env_file = ".env"

settings = Settings()
app = FastAPI(title=settings.app_name)

# Model discovery
def discover_models():
    """Scan MODEL_PATH directory and discover available models"""
    models = {}
    model_dir = Path(settings.model_path)

    if model_dir.exists() and model_dir.is_dir():
        for model_folder in model_dir.iterdir():
            if model_folder.is_dir():
                models[model_folder.name] = {
                    "path": str(model_folder),
                    "available": True
                }

    return models

# Load available models on startup
available_models = discover_models()

@app.get("/")
def read_root():
    return {
        "Message": "Hello World. This message indicates that this server is up and running.",
        "Display name": settings.display_name,
        "Model name": settings.model_name,
        "Server version": settings.server_version,
        "API version": settings.api_version,
        "Available models": list(available_models.keys())
    }

@app.get("/models")
def list_models():
    """List all available models"""
    return {
        "models": available_models,
        "count": len(available_models)
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

    return data

@app.post("/evaluate/azure")
async def evaluate(url: str = Query(...)):
    target_url = f"{settings.azure_route}?url={url}"
    print("Target url:", target_url)
    # Ascync calls ocr service
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(target_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calling OCR service: {str(e)}")

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="OCR service returned an error")

    return response.json()
@app.post("/evaluate/{model_name}")
async def evaluate_with_model(model_name: str, url: str = Query(...)):
    """Evaluate with a specific model (future implementation)"""
    if model_name not in available_models:
        raise HTTPException(
            status_code=404, 
            detail=f"Model '{model_name}' not found. Available models: {list(available_models.keys())}"
        )
    
    # Placeholder for actual model inference
    # This will be implemented when models are added to /app/models
    raise HTTPException(status_code=501, detail="Model inference not yet implemented")
