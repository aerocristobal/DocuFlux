#!/bin/bash
set -e

# Run warmup script in background (it keeps running to serve healthz)
python3 warmup.py &

# Wait for models to be ready before starting Celery?
# The story says "Worker Dockerfile adds ... to pre-cache ... so that first PDF conversion doesn't trigger slow downloads"
# And "Logs: 'Models cached... ETA 0s' on startup"
# If we block Celery until ready, the worker won't consume tasks.
# But if we don't block, the first task might start before warmup finishes (if it was slow).
# However, `warmup.py` is mostly for status reporting and ensuring downloads.
# The `Dockerfile` RUN instruction already did the heavy lifting.
# So `warmup.py` should be fast.
# We proceed to start Celery immediately.

# Execute the CMD passed to docker run (which is usually celery ...)
exec "$@"
