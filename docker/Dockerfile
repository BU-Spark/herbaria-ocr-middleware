# Dockerfile for running the server on a container
FROM python:3.11.5-slim

# Set working directory
WORKDIR /app

# Copy application files
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Expose the FastAPI port
EXPOSE 8000

# Run the application.
#
# No --reload: it is a development flag. In an immutable image the code cannot
# change, so the file watcher only burns cycles scanning /app. --reload is also
# incompatible with --workers, so it prevents configuring multiple worker
# processes and therefore limits process-level scaling.
#
# For local development use the documented workflow in README.md instead:
#   uvicorn main:app --reload
#
# Adding --workers N here is a separate question: main:app may hold per-process
# model state, which needs checking first.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
