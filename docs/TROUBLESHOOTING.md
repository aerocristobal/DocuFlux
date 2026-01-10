# Troubleshooting Guide

## Common Issues

### 1. "AI Service is currently unavailable"
**Symptom**: You see this warning in the UI, or "PDF (High Accuracy)" is disabled/warns on selection.
**Cause**: The `marker-api` container is unreachable or failing health checks.
**Solutions**:
- Check container status: `docker ps`. Is `pandoc-web-marker-api-1` running?
- Check logs: `docker logs pandoc-web-marker-api-1`.
- **Resource OOM**: Marker requires significant RAM (4GB-8GB+ depending on model/file). If the container exited with code 137, increase Docker memory limits.
- **Initialization**: On the first run, the model downloads several GBs of weights. This can take 5-10 minutes. Wait for "Application startup complete" in the logs.

### 2. "Server storage is full" (Error 507)
**Symptom**: Uploads fail immediately with a 507 error.
**Cause**: The volume mounted for data storage (default `data/`) has less than 500MB free.
**Solutions**:
- Prune old docker data: `docker system prune`.
- Check host disk space: `df -h`.
- Manually run cleanup (though the automatic task should handle this):
    - Enter worker: `docker exec -it pandoc-web-worker-1 bash`
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

## Logs & Debugging
To see detailed logs:
```bash
# Web Server
docker-compose logs -f web

# Celery Worker (Conversion logic)
docker-compose logs -f worker

# AI Service
docker-compose logs -f marker-api
```
