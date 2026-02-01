# DocuFlux

DocuFlux is a modern, containerized document conversion service that bridges the gap between traditional document formats and modern AI-ready workflows. It combines the versatility of **Pandoc** with the power of **AI-driven PDF analysis (Marker)** to provide high-fidelity conversions for everyone.

## Features

-   **🎨 Modern Material Design UI**: Built with Google's Material Design 3, featuring automatic dark mode synchronization and manual theme overrides.
-   **🤖 AI-Powered PDF Conversion**: Utilizes the **Marker** engine (deep learning) to convert PDFs into clean, structured Markdown. It intelligently detects if a GPU is available for high-speed processing and falls back to CPU-only mode otherwise.
-   **🧠 Local Document Intelligence**: Integrates a local Small Language Model (SLM) via `llama-cpp-python` to automatically extract semantic metadata (titles, summaries, tags) from your documents without external API calls.
-   **👁️ Vision-Based Extraction**: A dedicated **Model Context Protocol (MCP)** server running Playwright enables vision-capable models to interact with web pages, allowing for content extraction from DRM-protected readers or other web-based sources.
-   **🔄 Agentic Page Turning**: Autonomous multi-page document extraction from web readers using a "Code Mode" that generates and executes navigation scripts.
-   **⚡ Intelligent Ingestion**: Drag-and-drop interface with automatic format detection and smart defaulting.
-   **🔒 Comprehensive Security**:
    -   **HTTPS by Default**: Zero-touch HTTPS via Cloudflare Tunnel.
    -   **End-to-End Encryption**: Application-level encryption at rest (AES-256-GCM) for all files and sensitive metadata, plus encryption in transit (Redis TLS) for all inter-service communication.
    -   **Ephemeral by Design**: Strict, automated data retention policies ensure your data is purged after a short period (1 hour for un-downloaded files, 10 minutes for downloaded).
-   **🚀 High Performance & Observable**:
    -   Asynchronous task processing with Celery & Redis.
    -   Prometheus metrics endpoint (`/metrics`) for monitoring.
    -   Detailed health checks (`/healthz`, `/api/health`) for observability.
-   ** Wide Format Support**:
    -   **Inputs**: Markdown, HTML, Docx, LaTeX, Epub, ODT, BibTeX, Wiki formats, and more.
    -   **Outputs**: PDF (via LaTeX), Docx, Epub, HTML, Markdown, etc.

## Tech Stack

-   **Frontend**: HTML5, JavaScript, [Material Web Components](https://github.com/material-components/material-web) (@material/web).
-   **Backend**: Python 3.11, Flask.
-   **Task Queue**: Celery with Redis Broker.
-   **Conversion Engines & AI**:
    -   [Pandoc](https://pandoc.org/) (Universal document converter)
    -   [Marker](https://github.com/VikParuchuri/marker) (AI PDF processing)
    -   [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (Local LLM inference)
    -   [Playwright](https://playwright.dev/) (Browser automation for vision tasks)
-   **Infrastructure & Security**:
    -   Docker, Docker Compose, NVIDIA Container Toolkit.
    -   [Cloudflare Tunnel](https://www.cloudflare.com/products/tunnel/) (for HTTPS).
    -   [Certbot](https://certbot.eff.org/) (for automated certificate management).

## Prerequisites

-   **Docker** & **Docker Compose**
-   **(Optional) NVIDIA GPU**: For optimal performance with the AI PDF converter, an NVIDIA GPU and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) are recommended. The system falls back to CPU but will be significantly slower for PDF processing.

## Installation & Usage

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/docuflux.git
    cd docuflux
    ```

2.  **Configure Environment**:
    Create a `.env` file from the example and fill in the required values:
    ```bash
    cp .env.example .env
    # Edit .env and provide your details, especially for Cloudflare and Certbot.
    ```

3.  **Start the services:**
    -   **For GPU users (recommended):**
        ```bash
        docker-compose --profile gpu up -d --build
        ```
    -   **For CPU-only users:**
        ```bash
        docker-compose --profile cpu up -d --build
        ```
    *Note: The `worker` service downloads large AI models on the first build/run. Please be patient.*

4.  **Access the interface:**
    Open your browser and navigate to `http://localhost:5000` (or your configured Cloudflare domain if using the `https` profile).

## Architecture

The system follows a microservices pattern orchestrated by Docker Compose, with a focus on security and scalability.

| Service | Description |
| :--- | :--- |
| **`web`** | Flask frontend. Handles uploads, serves the UI, and dispatches jobs to the task queue. |
| **`worker`** | Celery worker. Executes standard Pandoc conversions, runs local Marker AI models for PDF extraction, and performs SLM-based metadata extraction. |
| **`mcp-server`** | A dedicated server running Playwright for browser automation, enabling vision-based extraction tasks. |
| **`redis`** | Message broker for the Celery task queue and ephemeral metadata store for job tracking. |
| **`beat`** | Celery beat scheduler for periodic cleanup tasks, ensuring data retention policies are met. |
| **`cloudflare-tunnel`** | Provides zero-touch HTTPS for the web service via Cloudflare's infrastructure. |
| **`certbot`** | Manages automated SSL/TLS certificate issuance and renewal using Let's Encrypt and Cloudflare DNS. |

The architecture supports both CPU and GPU-based deployments through Docker Compose profiles, allowing for flexible and resource-efficient operation. All inter-service communication is encrypted, and sensitive data is encrypted at rest.

## Data Retention Policy

To maintain a clean and secure environment, DocuFlux enforces the following automated cleanup rules:
-   **Completed (Downloaded)**: Deleted **10 minutes** after download.
-   **Completed (Not Downloaded)**: Deleted **1 hour** after creation.
-   **Failed Jobs**: Deleted **5 minutes** after failure.

## REST API

DocuFlux provides a REST API for programmatic integration with external tools and workflows. The API supports document conversion, job tracking, and result retrieval.

### Quick Start

```bash
# Submit a conversion job
curl -X POST http://localhost:5000/api/v1/convert \
  -F "file=@document.pdf" \
  -F "to_format=markdown" \
  -F "engine=marker"

# Response: {"job_id": "550e8400-...", "status": "queued", "status_url": "/api/v1/status/550e8400-..."}

# Check job status
curl http://localhost:5000/api/v1/status/550e8400-e29b-41d4-a716-446655440000

# Download result when completed
curl -O -J http://localhost:5000/api/v1/download/550e8400-e29b-41d4-a716-446655440000
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/convert` | POST | Submit document conversion job |
| `/api/v1/status/{job_id}` | GET | Check job status and progress |
| `/api/v1/download/{job_id}` | GET | Download converted file(s) |
| `/api/v1/formats` | GET | List supported formats and conversions |

### Parameters

**POST /api/v1/convert:**
- `file` (required): Document file to convert
- `to_format` (required): Target format (e.g., "markdown", "pdf", "docx")
- `from_format` (optional): Source format, auto-detected if omitted
- `engine` (optional): "pandoc" or "marker" (default: "pandoc")
- `force_ocr` (optional): Enable OCR for Marker (default: false)
- `use_llm` (optional): Use LLM for Marker (default: false)

**Response Codes:**
- `202 Accepted`: Job queued successfully
- `400 Bad Request`: Invalid request (missing file, format, etc.)
- `422 Unprocessable Entity`: Unsupported format conversion
- `507 Insufficient Storage`: Server storage full

### Authentication

The API uses the same IP-based rate limiting as the web UI (1000/day, 200/hour per IP). No authentication is required for basic usage. API endpoints are CSRF-exempt for REST compatibility.

### Testing

Run the integration test suite:
```bash
./tests/test_api_v1_integration.sh
```

For detailed API documentation, see [API Reference](docs/API.md).

## Documentation
- [Deployment Guide](docs/DEPLOYMENT.md)
- [API Reference (OpenAPI)](docs/openapi.yaml)
- [Supported Formats](docs/FORMATS.md)
- [AI Integration](docs/AI_INTEGRATION.md)
- [Certificate Management](docs/CERTIFICATE_MANAGEMENT.md)
- [Cloudflare API Setup](docs/CLOUDFLARE_API_API_SETUP.md)
- [Security Fixes](docs/SECURITY_FIXES.md)
- [Urgent Fixes](docs/URGENT_FIXES.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

## Development

### Project Structure
```
docuflux/
├── docker-compose.yml      # Orchestration config
├── web/                    # Flask Frontend
│   ├── app.py
│   └── templates/          # Material Design templates
├── worker/                 # Celery Worker
│   └── tasks.py            # Conversion logic
├── data/                   # Shared volume (Ignored)
└── tests/                  # Verification scripts
```

### Verification
To verify the system functionality:
```bash
python3 tests/verify_phase8.py
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.