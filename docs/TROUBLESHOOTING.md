# Troubleshooting Guide

## Common Issues

### 1. "AI Service is currently unavailable"
**Symptom**: You see this warning in the UI, or "PDF (High Accuracy)" is disabled/warns on selection.
**Cause**: Marker failed to initialize inside the worker, or the worker is not running.
**Solutions**:
- Check worker status: `docker ps`. Is the worker container running?
- Check worker logs: `docker-compose logs -f worker`. Marker and SLM initialization logs appear here.
- **Resource OOM**: Marker requires significant RAM (4GB-8GB+ depending on model/file). If the worker exited with code 137, increase Docker memory limits.
- **Initialization**: On the first run, the model downloads several GBs of weights. This can take 5-10 minutes. Wait for warmup to complete in the worker logs.

### 2. "Server storage is full" (Error 507)
**Symptom**: Uploads fail immediately with a 507 error.
**Cause**: The volume mounted for data storage (default `data/`) has less than 500MB free.
**Solutions**:
- Prune old docker data: `docker system prune`.
- Check host disk space: `df -h`.
- Manually run cleanup (though the automatic task should handle this):
    - Enter worker: `docker-compose exec worker bash`
    - Run Python shell and execute cleanup logic (advanced).

### 3. File Upload "Network Error" or 413
**Symptom**: Upload stops instantly or returns "File too large".
**Cause**: File exceeds 100MB limit.
**Solution**: Split the document or compress images.

### 4. Conversion Timeout (Error 500 / "Job Failed")
**Symptom**: Job stays in "Processing" for > 10 minutes then fails.
**Cause**: Complex documents (huge PDFs or huge Word files) exceeded the time limit.
**Timeouts**:
- Standard: 10 minutes.
- AI/Marker: 20 minutes.
**Solution**: Simplify the document or run locally using CLI tools if possible.

### 5. "Pandoc failed: xelatex not found"
**Symptom**: PDF generation fails.
**Cause**: The Docker image might be missing specific LaTeX fonts or packages.
**Solution**:
- We use the `pandoc/latex:3.1` image which includes a full TeXLive distribution. If a specific font is missing, it cannot be added dynamically. Ensure your document uses standard fonts.

### 6. Redis Connection Errors
**Symptom**: Worker or web service fails to start with Redis connection errors.
**Cause**: Redis container not running or unreachable.
**Solutions**:
- Check Redis is running: `docker-compose ps redis`
- Check Redis logs: `docker-compose logs redis`
- Verify `REDIS_METADATA_URL` and `CELERY_BROKER_URL` are correct in your environment.

### 7. GPU Not Detected
**Symptom**: Worker logs show "No GPU detected, using CPU" even though you have a GPU.
**Cause**: NVIDIA Container Toolkit not installed, or wrong compose file used.
**Solutions**:
- Verify NVIDIA runtime: `nvidia-smi` on the host
- Use the GPU compose overlay: `docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up`
- See [BUILD.md](../BUILD.md) for GPU build setup.

### 8. Worker Out of Memory
**Symptom**: Worker container exits with code 137 during AI conversion.
**Cause**: Marker + SLM models exceed container memory limit.
**Solutions**:
- Increase Docker memory limits (16GB+ recommended for GPU builds)
- Use CPU-only build if GPU memory is insufficient
- Reduce `MAX_MARKER_PAGES` to limit PDF processing scope

## Logs & Debugging
To see detailed logs:
```bash
# Web Server
docker-compose logs -f web

# Celery Worker (Pandoc, Marker, and SLM logs all appear here)
docker-compose logs -f worker

# Redis
docker-compose logs -f redis

# All services
docker-compose logs -f
```
