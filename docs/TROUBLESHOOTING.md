# Troubleshooting Guide

## Common Issues

### 1. "AI Service is currently unavailable"
**Symptom**: You see this warning in the UI, or "PDF (High Accuracy)" is disabled/warns on selection.
**Cause**: Under Marker v2 the worker is only a thin client; the OCR/layout model runs in the separate `surya-vlm` inference server. The warning means that server is not (yet) reachable, the worker is not running, or Surya is intentionally off (`OCR_MODE=cpu_only`).
**Solutions**:
- Check the services: `docker-compose ps`. Are `worker`, `surya-controller` and `docker-socket-proxy` running?
- In the default `OCR_MODE=on_demand`, `surya-vlm` is stopped while idle and started by the controller when a Marker job arrives. See entries 9-13 below before treating `exited` or `unreachable` as a fault.
- Check the inference server: `docker-compose logs -f surya-vlm` and, while it is up, `docker-compose exec surya-vlm python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').status)"` (503 until the model has loaded, then 200).
- Check the worker logs: `docker-compose logs -f worker`. SLM and inference-server probe messages appear here.
- Confirm `SURYA_INFERENCE_URL` ends in `/v1` (`http://surya-vlm:8000/v1`). Without the suffix `/health` still answers 200 but every inference request 404s and Marker emits empty output.
- **Model weights**: the first start of `surya-vlm` downloads `datalab-to/surya-ocr-2`; allow extra time (the container healthcheck has a 300 s start period).

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
**Cause**: File exceeds the upload limit (200 MB by default, `MAX_CONTENT_LENGTH`).
**Solution**: Split the document or compress images, or raise `MAX_CONTENT_LENGTH` (bytes) for the `web` service.

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
- The worker image installs Pandoc and TeX Live packages (`texlive-latex-base`, `texlive-latex-extra`, XeLaTeX) with apt, not a prebuilt Pandoc image. If a specific font or LaTeX package is missing, add it to `worker/Dockerfile` and rebuild; it cannot be added to a running container. Otherwise ensure your document uses standard fonts.

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
**Cause**: The SLM (and local PDF/image processing) exceeded the worker's memory limit. The Marker/Surya weights are in the `surya-vlm` container, not the worker.
**Solutions**:
- Increase the worker's Docker memory limit (16GB in the base compose file)
- If `surya-vlm` itself exits with 137 or vLLM reports CUDA out-of-memory, lower `VLLM_GPU_MEMORY_UTILIZATION` (0.85 by default) or free VRAM used by other processes
- Use the CPU overlay with `OCR_MODE=cpu_only` if GPU memory is insufficient
- Reduce `MAX_MARKER_PAGES` to limit PDF processing scope

### 9. `surya-vlm` shows `Exited` in `docker ps`
**Symptom**: `docker-compose ps` lists `surya-vlm` as exited (often with a clean stop code) while the rest of the stack is healthy.
**Cause**: Normal in `OCR_MODE=on_demand`. `surya-controller` stops the container after `SURYA_IDLE_TIMEOUT_MINUTES` (default 15) with no Marker work, to free VRAM; the `unless-stopped` restart policy respects an explicit stop.
**Solution**: Nothing to fix. It starts again on the next Marker job, or load it manually with `POST /api/v1/admin/surya` (`{"action": "load"}`, `Authorization: Bearer $ADMIN_API_SECRET`), which also pins it against idle unload. Check `GET /api/v1/admin/surya` for `state`, `desired`, `pinned`, `mode`.

### 10. First Marker job after idle is slow (Surya cold start) and logs "Inference server probe failed"
**Symptom**: The job sits at "Starting AI engine" for about 3 minutes (observed on an RTX 3090). The worker log repeats `Inference server probe failed (...)` warnings every 10 seconds (name resolution error, then connection refused) until vLLM becomes healthy.
**Cause**: The controller is starting `surya-vlm` and vLLM must load the model; the worker's warmup probe warns while the server is not yet answering. The warnings are harmless noise during a normal start (tracked as bead DocuFlux-brf).
**Solutions**:
- Wait; the job proceeds once `/health` returns 200.
- If it fails with "The AI engine did not start within N seconds", raise `SURYA_COLD_START_TIMEOUT_SECONDS` (default 600) and inspect `docker-compose logs surya-vlm`.
- A probe warning that persists after `GET /api/v1/admin/surya` reports `state: running` is a real outage.

### 11. Marker formats are rejected (HTTP 409) or hidden in the UI
**Symptom**: Choosing "PDF (High Accuracy)", Hybrid or AI + SLM returns `409 AI engine disabled (OCR mode is cpu_only); use the OCR engine instead`, or those formats do not appear.
**Cause**: `OCR_MODE=cpu_only` (or the mode was switched at runtime through `PUT /api/v1/admin/ocr-settings`). Surya is never started in this mode.
**Solution**: Use "PDF (OCR - CPU)" (`pdf_ocr`, Tesseract), or switch to `on_demand` on a GPU host: `PUT /api/v1/admin/ocr-settings` with `{"mode": "on_demand"}`, or set `OCR_MODE=on_demand`. Note the runtime setting in Redis (`settings:ocr`) overrides the environment seed until changed.

### 12. Manual load returns 409 (no suitable GPU, or cpu_only)
**Symptom**: `POST /api/v1/admin/surya` with `{"action": "load"}` returns `409`.
**Cause**: Either `Surya VLM is not supported on this host (no GPU with enough VRAM)` (Surya needs a GPU with at least 16 GB total VRAM, as reported by the worker's GPU detection in `marker:gpu_info`) or `OCR mode is cpu_only`.
**Solutions**: Check `GET /api/v1/admin/surya` (`surya_supported`, `mode`) and `/api/status/services` (`gpu_info`). If the GPU is present but `surya_supported` is false, confirm the GPU worker is running and has finished warmup (the keys expire if the worker dies), and that the NVIDIA Container Toolkit is installed.

### 13. Controller or socket-proxy problems (`surya-vlm` never starts or stops)
**Symptom**: Marker jobs time out waiting for the AI engine, or `GET /api/v1/admin/surya` shows `desired: running` but `state` never leaves `stopped` or `starting` (or shows `error`).
**Cause**: `surya-controller` is the only component that starts and stops `surya-vlm`, through `docker-socket-proxy`. If either is down, desired state is never reconciled. The CPU overlay deliberately disables both.
**Solutions**:
- `docker-compose ps surya-controller docker-socket-proxy` and `docker-compose logs surya-controller docker-socket-proxy`.
- Confirm you did not start with `docker-compose.cpu.yml` on a GPU host.
- Check the proxy allowlist against the real image: `deploy/docker-socket-proxy/verify.sh` (expects `start`/`stop` and `containers/json` forwarded, `create`, `exec`, `kill`, `restart` denied with 403).
- The controller finds the container by the label `com.docker.compose.service=surya-vlm`; if you changed the Compose project name, set `COMPOSE_PROJECT_NAME` for the controller.
- With Redis TLS (`docker-compose.tls.yml`), the controller and `worker-light` still use plaintext `redis://…:6379` and cannot connect to the TLS-only Redis; see [CERTIFICATE_MANAGEMENT.md](CERTIFICATE_MANAGEMENT.md).

## Logs & Debugging
To see detailed logs:
```bash
# Web Server
docker-compose logs -f web

# Celery heavy-lane worker (Marker client and SLM logs)
docker-compose logs -f worker

# Light-lane worker (Pandoc, OCR, metadata, maintenance)
docker-compose logs -f worker-light

# Surya inference server and its on-demand lifecycle
docker-compose logs -f surya-vlm surya-controller docker-socket-proxy

# Redis
docker-compose logs -f redis

# All services
docker-compose logs -f
```
