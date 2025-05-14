# herbaria-ocr-server

A middleware for running AI Optical Character Recognition (OCR) inferences on images sent from the herbaria portal. This is a FastAPI app.

## API documentation
FastAPI docs are accessible at http://127.0.0.1:8000/docs by default.

## Running the middleware
1. Create a `config.yaml` file in `/config` based on the template file.
2. In `docker/docker-compose.yaml`, customize the docker network if needed.
3. Navigate to the `docker` directory and run `docker compose up -d`.