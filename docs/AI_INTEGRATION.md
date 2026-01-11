# AI Integration: Marker PDF

This project integrates [Marker](https://github.com/VikParuchuri/marker), a high-accuracy PDF-to-Markdown converter powered by deep learning models (OCR, Layout Analysis).

## Architecture
- **Integration**: Direct library integration via `marker-pdf` Python package
- **Location**: Installed in the `worker` container
- **Communication**: Direct subprocess call to `marker_single` CLI
- **GPU**: Shared with worker container (NVIDIA CUDA)

## Usage
When a user selects **"PDF (High Accuracy)"** (`pdf_marker`) as the input format:
1. The web app queues a `tasks.convert_with_marker` Celery task.
2. The worker runs `marker_single` CLI directly with the PDF file.
3. Marker processes the file (GPU accelerated if available).
4. Marker generates Markdown output in a temporary directory.
5. The worker copies the markdown to the final output location.

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
