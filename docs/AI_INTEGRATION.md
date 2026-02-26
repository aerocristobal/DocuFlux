# AI Integration: Marker PDF

This project integrates [Marker](https://github.com/VikParuchuri/marker), a high-accuracy PDF-to-Markdown converter powered by deep learning models (OCR, Layout Analysis).

## Architecture
- **Integration**: Direct library integration via `marker-pdf` Python package
- **Location**: Installed in the `worker` container
- **Communication**: Direct Python API call (`PdfConverter`)
- **GPU**: Shared with worker container (NVIDIA CUDA)

## Usage
When a user selects **"PDF (High Accuracy)"** (`pdf_marker`) as the input format:
1. The web app queues a `tasks.convert_with_marker` Celery task.
2. The worker initializes the Marker models (cached after first use) and runs conversion in-process.
3. Marker processes the file (GPU accelerated if available).
4. Marker generates output objects which are serialized to disk (markdown, images, metadata).
5. The worker organizes these into the final output directory.

## Fallback & Resilience
Since AI inference is heavy and can fail:
1. **Retry Logic**: Failed conversions are retried up to 3 times automatically.
2. **Timeouts**: AI jobs have a higher timeout (20 minutes) compared to standard jobs (10 minutes).
3. **Error Handling**: Subprocess errors are captured and reported to the user with detailed error messages.

## Technical Details
- **Models**: Uses `surya` for OCR/Layout. Weights are cached in `/app/models` inside the container.
- **GPU**: Passed through via `deploy.resources.reservations.devices`.
- **Memory**: Configured with a high limit (16GB) in `docker-compose.yml` to prevent OOM kills during PyTorch inference.

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

If the file is absent, SLM extraction is silently skipped (`slm_status: SKIPPED`) and all other functionality is unaffected.

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
