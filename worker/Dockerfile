FROM pandoc/latex:3.1 AS base

# Install Python and pip
RUN apk add --no-cache python3 py3-pip

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --break-system-packages

COPY . .

# Reset entrypoint (pandoc/latex sets it to pandoc binary)
ENTRYPOINT []

# Run celery worker with concurrency limit
CMD ["celery", "-A", "tasks", "worker", "--loglevel=info", "--concurrency=2"]
