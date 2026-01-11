# AI Integration: Marker API

This project integrates [Marker](https://github.com/VikParuchuri/marker), a high-accuracy PDF-to-Markdown converter powered by deep learning models (OCR, Layout Analysis).

## Architecture
- **Service**: `marker/` (standalone Dockerfile using [adithya-s-k/marker-api](https://github.com/adithya-s-k/marker-api))
- **Container**: `marker-api`
- **Communication**: HTTP (REST)
- **Endpoint**: `POST http://marker-api:8000/convert`

## Usage
When a user selects **"PDF (High Accuracy)"** (`pdf_marker`) as the input format:
1. The web app queues a `tasks.convert_with_marker` Celery task.
2. The worker sends the PDF file to the `marker-api` container.
3. Marker processes the file (GPU accelerated if available).
4. Marker returns the extracted Markdown content.
5. The worker saves this as the output `.md` file.

## Fallback & Resilience
Since AI inference is heavy and the service might be fragile (OOM, startup time):
1. **Queuing**: If the API is busy (503) or unreachable (Connection Error), the worker **retries** the task automatically with exponential backoff for up to ~5 minutes.
2. **UI Feedback**: The frontend polls the status endpoint `/api/status/services`. If Marker is down, the user is warned *before* upload, but allowed to queue the job (knowing it will wait).
3. **Timeouts**: AI jobs have a higher timeout (20 minutes) compared to standard jobs (10 minutes).

## Technical Details
- **Models**: Uses `surya` for OCR/Layout. Weights are cached in `/app/models` inside the container.
- **GPU**: Passed through via `deploy.resources.reservations.devices`.
- **Memory**: Configured with a high limit (16GB) in `docker-compose.yml` to prevent OOM kills during PyTorch inference.

## Troubleshooting
See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for specific AI-related issues.
