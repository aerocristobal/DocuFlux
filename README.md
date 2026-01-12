# DocuFlux

DocuFlux is a modern, containerized document conversion service that bridges the gap between traditional document formats and modern AI-ready workflows. It combines the versatility of **Pandoc** with the power of **AI-driven PDF analysis (Marker)** to provide high-fidelity conversions for everyone.

## Features

-   **🎨 Modern Material Design UI**: Built with Google's Material Design 3, featuring automatic dark mode synchronization and manual theme overrides.
-   **🤖 AI-Powered PDF Conversion**: Utilizes the **Marker** engine (deep learning) to convert PDFs into clean, structured Markdown, preserving tables, equations, and layout better than traditional tools.
-   **⚡ Intelligent Ingestion**: Drag-and-drop interface with automatic format detection and smart defaulting (e.g., auto-selecting AI mode for PDFs).
-   **🔄 Wide Format Support**:
    -   **Inputs**: Markdown, HTML, Docx, LaTeX, Epub, ODT, BibTeX, Wiki formats, and more.
    -   **Outputs**: PDF (via LaTeX), Docx, Epub, HTML, Markdown, etc.
-   **🔒 Ephemeral & Secure**: Strict data retention policy. Output files are automatically deleted after **1 hour** (or 10 minutes if downloaded), ensuring privacy.
-   **🚀 High Performance**: Asynchronous task processing with Celery & Redis, supporting concurrent conversions and heavy workloads.

## Tech Stack

-   **Frontend**: HTML5, JavaScript, [Material Web Components](https://github.com/material-components/material-web) (@material/web).
-   **Backend**: Python 3.11, Flask.
-   **Task Queue**: Celery with Redis Broker.
-   **Conversion Engines**:
    -   [Pandoc](https://pandoc.org/) (Universal converter)
    -   [Marker](https://github.com/VikParuchuri/marker) (AI PDF processing, embedded in Worker)
-   **Infrastructure**: Docker, Docker Compose, NVIDIA Container Toolkit.

## Prerequisites

-   **Docker** & **Docker Compose**
-   **(Optional) NVIDIA GPU**: For optimal performance with the AI PDF converter, an NVIDIA GPU and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) are recommended. The system falls back to CPU but will be significantly slower for PDF processing.

## Installation & Usage

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/docuflux.git
    cd docuflux
    ```

2.  **Start the services:**
    ```bash
    docker-compose up -d --build
    ```
    *Note: The `worker` service downloads large AI models (~3GB) on the first build/run. Please be patient.*

3.  **Access the interface:**
    Open your browser and navigate to `http://localhost:5000`.

## Architecture

The system follows a microservices pattern orchestrated by Docker Compose:

| Service | Description |
| :--- | :--- |
| **`web`** | Flask frontend. Handles uploads, serves the UI, and dispatches jobs. |
| **`worker`** | Celery worker. Executes standard Pandoc conversions and runs local Marker AI models for PDF extraction. |
| **`redis`** | Message broker for the task queue and ephemeral metadata store. |
| **`beat`** | Scheduler for periodic cleanup tasks. |

## Data Retention Policy

To maintain a clean and secure environment, DocuFlux enforces the following automated cleanup rules:
-   **Completed (Downloaded)**: Deleted **10 minutes** after download.
-   **Completed (Not Downloaded)**: Deleted **1 hour** after creation.
-   **Failed Jobs**: Deleted **5 minutes** after failure.

## Documentation
- [Deployment Guide](docs/DEPLOYMENT.md)
- [API Reference (OpenAPI)](docs/openapi.yaml)
- [Supported Formats](docs/FORMATS.md)
- [AI Integration](docs/AI_INTEGRATION.md)
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