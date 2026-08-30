# AI Integration: Marker PDF

This project integrates [Marker](https://github.com/VikParuchuri/marker), a high-accuracy PDF-to-Markdown converter powered by deep learning models (OCR, Layout Analysis), via the `marker-pdf[full]==2.0.0` package.

## Architecture (Marker v2 — client/server)
- **Integration**: `marker-pdf 2.0.0` thin client installed in the `worker` container
- **Model weights**: Live in the shared **`surya-vlm`** inference service — a [vLLM](https://github.com/vllm-project/vllm) server (`vllm/vllm-openai` image) serving `datalab-to/surya-ocr-2` — not in the worker
- **Communication**: The worker builds a `PdfConverter(artifact_dict=...)` and the Marker client sends OCR/layout inference requests over HTTP to `SURYA_INFERENCE_URL` (default `http://surya-vlm:8000`, internal port 8000 only)
- **Worker attachment**: `SURYA_INFERENCE_AUTOSTART=false` — the deployment owns the server; the worker attaches to it lazily and never spawns or stops its own
- **GPU**: Owned by the `surya-vlm` container (NVIDIA device reservation); VRAM share is sized server-side via `VLLM_GPU_MEMORY_UTILIZATION` (`0.85` in the base compose file)

## Usage
When a user selects **"PDF (High Accuracy)"** (`pdf_marker`) as the input format:
1. The web app queues a `tasks.convert_with_marker` Celery task.
2. The worker obtains the cached Marker artifact dict — lazily created via `create_model_dict()` on the first conversion per worker process. This is a thin-client call (~1 s, vs ~30–70 s to load weights in-process under v1).
3. The `PdfConverter` runs the conversion, delegating OCR/layout inference to the `surya-vlm` server over HTTP (GPU-accelerated on the server side).
4. Marker generates output objects which are serialized to disk (markdown, images, metadata).
5. The worker organizes these into the final output directory.

## Fallback & Resilience
Since AI inference is heavy and can fail:
1. **Retry Logic**: Failed conversions are retried up to 3 times automatically.
2. **Timeouts**: AI jobs have a higher timeout (20 minutes) compared to standard jobs (10 minutes).
3. **Error Handling**: Errors are captured and reported to the user with detailed error messages.
4. **Degeneration guard**: Output quality is scored after every conversion; a document exceeding the `excess_output` ceilings (2,000 words/page or 10,000 chars/page — see `shared/quality.py`) is flagged. This bounds the silent repetition-loop failure mode observed once during the v1→v2 benchmark (see `benchmarks/marker_v2/REPORT.md`).

## Artifact Lifecycle (per worker process)
- The artifact dict is created **lazily** by `get_model_dict()` on the first Marker conversion in each worker process and cached for that process's lifetime (`max_tasks_per_child=50` recycles the process, and with it the dict).
- After **every** conversion, `_cleanup_marker_memory()` releases only the task's local objects (converter, rendered result, images) via `gc.collect()`. Model weights are never touched — they live in the `surya-vlm` process.
- At **worker shutdown**, `shutdown_marker_models()` is called via the Celery `worker_shutdown` signal. When attached to a remote server (`SURYA_INFERENCE_URL` set, `AUTOSTART=false`) it is a no-op: the worker does not own the server.
- The **server itself is owned by the deployment**: operators recycle it on a bounded schedule (see ["Inference server recycling" in ARCHITECTURE.md](ARCHITECTURE.md#inference-server-recycling)) — never from the worker.

## Technical Details
- **Server**: `surya-vlm` runs the `vllm/vllm-openai` image with `SURYA_MODEL=datalab-to/surya-ocr-2`, listens on internal port 8000, and exposes `/health` (503 until the model is loaded, then 200; the compose healthcheck allows a 300 s start period for the first load).
- **Worker env**: `SURYA_INFERENCE_URL=http://surya-vlm:8000` and `SURYA_INFERENCE_AUTOSTART=false` in `docker-compose.yml`, `docker-compose.gpu.yml`, and `deploy/k8s/worker.yaml`.
- **Warmup probe**: `worker/warmup.py` probes `{SURYA_INFERENCE_URL}/health` at startup and every 10 s thereafter, storing `reachable`/`unreachable` in Redis for the web tier's service status and the worker `/healthz` payload.
- **Worker memory**: A high worker limit (16 GB) remains in `docker-compose.yml` for local PDF/image processing; GPU memory belongs to the `surya-vlm` service (24 GB baseline at `VLLM_GPU_MEMORY_UTILIZATION=0.85`).

## Troubleshooting
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for specific AI-related issues.

---

# AI Integration: SLM Metadata Extraction

DocuFlux integrates a local Small Language Model (SLM) via [`llama-cpp-python`](https://github.com/abetlen/llama-cpp-python) to automatically extract semantic metadata from converted documents—without any external API calls.

## What It Does

After a successful Marker PDF conversion the worker automatically runs SLM inference to extract:
- **Title** – a short, descriptive title for the document
- **Summary** – a one-sentence abstract
- **Tags** – up to five keyword tags

These fields appear in the Web UI job list and are available in the API response under `slm_metadata`.

## Architecture

- **Model**: [TinyLlama-1.1B-Chat-v1.0 Q4_K_M GGUF](https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF) (~670 MB)
- **Runtime**: `llama-cpp-python` (CPU or GPU via CUDA)
- **Location**: `/app/models/TinyLlama-1.1B-Chat-v1.0-GGUF/` inside the worker container
- **Trigger**: Automatically after every `convert_with_marker` success; also manually via `POST /api/v1/jobs/<id>/extract-metadata`

## Model Download

The model is **not bundled in the Docker image** (disabled to keep build times short). You must supply it before starting the worker.

### Option A — Download at build time (recommended for production)

Uncomment the `RUN` block in `worker/Dockerfile` (lines 67-73):

```dockerfile
RUN if [ "$BUILD_GPU" = "true" ]; then \
        echo "Downloading default SLM model for GPU build..."; \
        mkdir -p /app/models && \
        git clone https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF \
            /app/models/TinyLlama-1.1B-Chat-v1.0-GGUF; \
    else \
        echo "Skipping SLM model download for CPU-only build"; \
    fi
```

This clones the full HuggingFace repo (~670 MB) into the image at build time.

### Option B — Mount a pre-downloaded model (fastest for development)

1. Download the GGUF file on your host:

```bash
mkdir -p models/TinyLlama-1.1B-Chat-v1.0-GGUF
curl -L -o models/TinyLlama-1.1B-Chat-v1.0-GGUF/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf \
  https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
```

2. Mount it into the worker container in your `docker-compose.override.yml`:

```yaml
services:
  worker:
    volumes:
      - ./models:/app/models:ro
```

### Option C — Use a custom model

Set `SLM_MODEL_PATH` to the full path of any GGUF file inside the container:

```yaml
services:
  worker:
    environment:
      SLM_MODEL_PATH: /app/models/my-custom-model.gguf
    volumes:
      - ./models:/app/models:ro
```

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `SLM_MODEL_PATH` | _(none)_ | Full path to a GGUF model file. Overrides the default TinyLlama path. |

When `SLM_MODEL_PATH` is unset, the worker looks for the model at:
`/app/models/TinyLlama-1.1B-Chat-v1.0-GGUF/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf`

The model is loaded **eagerly at worker startup** (inside `warmup()`), not on first use. This means:
- ~670 MB of RAM is consumed from the moment the worker starts, regardless of job volume.
- If the file is absent at startup, SLM is permanently disabled for that worker process — adding the file later requires a worker restart.
- There is no warm-up delay on first extraction; inference starts immediately.

## API Usage

Manually trigger SLM extraction for an already-completed job (requires API key):

```bash
curl -X POST http://localhost:5000/api/v1/jobs/<job_id>/extract-metadata \
  -H "X-API-Key: <your-api-key>"
```

Check status via `/api/v1/status/<job_id>`:

```json
{
  "slm_metadata": {
    "status": "SUCCESS",
    "title": "Introduction to Quantum Computing",
    "summary": "A primer on qubits, superposition, and entanglement.",
    "tags": ["quantum", "computing", "qubits", "physics"]
  }
}
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `slm_status: SKIPPED` | Model file not found | Download the model (see above) |
| `slm_status: FAILURE` | Inference error | Check worker logs; model may be corrupted |
| `slm_status: not_found` in Redis | warmup found no model | Same as SKIPPED — supply the model file |
| High memory usage | Model loaded in-process | Use a smaller GGUF quantisation (Q2_K) or CPU-only |
