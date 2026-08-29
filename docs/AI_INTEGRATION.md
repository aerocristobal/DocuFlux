# AI Integration: Marker PDF

This project integrates [Marker](https://github.com/VikParuchuri/marker), a high-accuracy PDF-to-Markdown converter powered by deep learning models (OCR, Layout Analysis).

## Architecture (marker 2 — client/server)

- **Integration**: `marker-pdf` 2.0.0 library, split into a thin worker client and a shared inference server
- **Location**: client in the `worker` container; server in a dedicated `surya-vlm` service (vLLM serving `datalab-to/surya-ocr-2`)
- **Communication**: worker calls `PdfConverter` (Python API); the VLM weights live in the `surya-vlm` server, which the worker attaches to via `SURYA_INFERENCE_URL` (`SURYA_INFERENCE_AUTOSTART=false` — the worker never spawns a server)
- **GPU**: owned by the `surya-vlm` service; the worker holds only small local models
- **Lifecycle**: model loading is ~1 s (attach), not a per-worker model download; the shared server is stopped at worker exit via `shutdown_marker_models()`, which is a no-op when the worker did not spawn it

## Usage
When a user selects **"PDF (High Accuracy)"** (`pdf_marker`) as the input format:
1. The web app queues a `tasks.convert_with_marker` Celery task.
2. The worker builds a thin artifact dict (`create_model_dict()`) that attaches to the Surya VLM inference server.
3. `PdfConverter` processes the file; the VLM does OCR/layout on the server (GPU accelerated).
4. Marker generates output objects which are serialized to disk (markdown, images, metadata).
5. The worker organizes these into the final output directory.

## Fallback & Resilience
Since AI inference is heavy and can fail:
1. **Retry Logic**: Failed conversions are retried up to 3 times automatically.
2. **Timeouts**: AI jobs have a higher timeout (20 minutes) compared to standard jobs (10 minutes).
3. **Error Handling**: Errors are captured and reported to the user with detailed error messages.

## Technical Details
- **Models**: The VLM (`datalab-to/surya-ocr-2`) runs in the `surya-vlm` vLLM service and is cached/downloaded by that service, not the worker.
- **GPU**: Reserved for the `surya-vlm` service (`deploy.resources.reservations.devices` in `docker-compose.gpu.yml`).
- **Memory**: `surya-vlm` is configured with a 24 GB memory limit at `VLLM_GPU_MEMORY_UTILIZATION=0.85`; the worker itself no longer holds the VLM weights.

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
