# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Install curl for healthcheck and libmagic1 for mime type validation
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends curl libmagic1

WORKDIR /app

COPY web/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Copy shared root-level modules first, then web app
COPY config.py .
COPY shared/ .
COPY web/ .

# Epic 21.8: Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# Use gunicorn with eventlet for WebSocket support (no monkey-patching conflicts with PyTorch)
CMD ["gunicorn", "--worker-class", "eventlet", "-w", "1", "--bind", "0.0.0.0:5000", "--access-logfile", "-", "app:app"]
