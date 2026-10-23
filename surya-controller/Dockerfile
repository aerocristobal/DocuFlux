# Surya lifecycle controller (epic pfm). Build context is the repo root so the
# shared Redis key module can be reused:
#   docker build -f surya-controller/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY surya-controller/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY surya-controller/main.py ./main.py
COPY shared/surya_state.py ./surya_state.py

# Non-root; the controller only talks TCP to docker-socket-proxy and Redis.
RUN useradd --system --uid 10001 --no-create-home controller
USER 10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "main.py", "--healthcheck"]

CMD ["python", "main.py"]
