# Conditional Dockerfile for GPU or CPU-only builds
# Build with: docker build --build-arg BUILD_GPU=true/false -t worker .
# Or use: ./scripts/build.sh auto

# Build argument to control GPU vs CPU build
ARG BUILD_GPU=true

# Stage 1: Base image selection (GPU with CUDA or CPU-only Ubuntu)
FROM nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 AS base-true
FROM ubuntu:22.04 AS base-false

# Select base based on BUILD_GPU argument
FROM base-${BUILD_GPU} AS base

# Avoid interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies: Pandoc, LaTeX, Python, Git-LFS, and OpenCV dependencies
RUN apt-get update && apt-get install -y \
    pandoc \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    python3 \
    python3-pip \
    git \
    git-lfs \
    curl \
    libgl1 \
    libglib2.0-0 \
    && git lfs install \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pass BUILD_GPU to runtime
ARG BUILD_GPU
ENV BUILD_GPU=${BUILD_GPU}



# Copy appropriate requirements file based on build mode
COPY requirements-true.txt requirements-build.txt
RUN pip3 install --upgrade pip setuptools
RUN pip3 install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cu118 -r requirements-build.txt

# Copy worker code
COPY . .

# Conditional SLM model download (only for GPU builds)
RUN if [ "$BUILD_GPU" = "true" ]; then \
        echo "Downloading default SLM model for GPU build..."; \
        mkdir -p /app/models && \
        git clone https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF /app/models/TinyLlama-1.1B-Chat-v1.0-GGUF; \
    else \
        echo "Skipping SLM model download for CPU-only build"; \
    fi

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Epic 21.8: Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser && \
    chown -R appuser:appuser /app
USER appuser

# Set entrypoint
ENTRYPOINT ["./entrypoint.sh"]

# Run celery worker (command overridden in docker-compose.yml)
CMD ["celery", "-A", "tasks", "worker", "--loglevel=info", "--pool=solo"]
