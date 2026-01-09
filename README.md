# Pandoc Web

A powerful, containerized web interface for converting documents between various formats using [Pandoc](https://pandoc.org/). This application provides a user-friendly frontend to upload files, select conversion formats, and process them asynchronously.

## Features

-   **Wide Format Support**: Convert between Markdown, HTML, Microsoft Word (`.docx`), PowerPoint (`.pptx`), LaTeX, PDF, EPUB, and many more.
-   **Asynchronous Processing**: Handles large files and complex conversions in the background using Celery workers.
-   **Job Management**: Track job status (Queued, Processing, Success, Failed) in real-time.
-   **Automatic Cleanup**: Automatically removes uploaded and generated files after 24 hours to manage storage.
-   **Dockerized**: Easy to deploy with Docker Compose.

## Supported Formats

The application supports a wide range of formats including:
-   **Markdown**: Pandoc Markdown, GitHub Flavored Markdown
-   **Web**: HTML5, Jupyter Notebook
-   **Office**: Microsoft Word (`docx`), PowerPoint (`pptx` - output only), OpenOffice/LibreOffice (`odt`), RTF
-   **E-Books**: EPUB (v2, v3)
-   **Technical**: LaTeX, PDF (via LaTeX), AsciiDoc, reStructuredText, BibTeX
-   **Wiki**: MediaWiki, Jira Wiki

## Prerequisites

-   Docker
-   Docker Compose

## Installation & Usage

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/pandoc-web.git
    cd pandoc-web
    ```

2.  **Start the application:**
    ```bash
    docker-compose up --build -d
    ```

3.  **Access the web interface:**
    Open your browser and navigate to `http://localhost:5000`.

## Architecture

The project is built using a microservices architecture:

-   **Web Service (`web/`)**: A Flask application that serves the UI, handles file uploads, and manages job queues.
-   **Worker Service (`worker/`)**: A Celery worker that executes the actual Pandoc conversion tasks.
-   **Redis**: Acts as the message broker and result backend for Celery.
-   **Celery Beat**: Schedules periodic tasks, such as cleaning up old files.

## Configuration

-   **Environment Variables**:
    -   `CELERY_BROKER_URL`: URL for the Celery broker (default: `redis://redis:6379/0`).
    -   `CELERY_RESULT_BACKEND`: URL for the Celery result backend (default: `redis://redis:6379/0`).
    -   `SECRET_KEY`: Flask secret key for session management.

## Development

The project structure is as follows:

```
pandoc-web/
├── docker-compose.yml   # Docker Compose configuration
├── web/                 # Web application code
│   ├── app.py           # Flask app entry point
│   ├── templates/       # HTML templates
│   └── Dockerfile       # Web service Dockerfile
├── worker/              # Worker application code
│   ├── tasks.py         # Celery tasks
│   └── Dockerfile       # Worker service Dockerfile
└── data/                # Shared volume for file storage
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
