# Conditional Dockerfile for GPU or CPU-only builds
# Build with: docker build --build-arg BUILD_GPU=true/false -t worker .
# Or use: ./scripts/build.sh auto

# Build argument to control GPU vs CPU build
ARG BUILD_GPU=true

# Stage 1: Base image selection (GPU with CUDA or CPU-only Ubuntu)
FROM nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 AS base-gpu
FROM ubuntu:22.04 AS base-cpu

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

# Conditional PyTorch installation
RUN if [ "$BUILD_GPU" = "true" ]; then \
        echo "Installing PyTorch with CUDA 11.8 support..."; \
        pip3 install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118; \
    else \
        echo "Installing PyTorch CPU-only version..."; \
        pip3 install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu; \
    fi

# Copy appropriate requirements file based on build mode
COPY requirements-${BUILD_GPU}.txt requirements-build.txt
RUN pip3 install --no-cache-dir -r requirements-build.txt

# Copy worker code
COPY . .

# Conditional Marker model pre-caching (only for GPU builds)
RUN if [ "$BUILD_GPU" = "true" ]; then \
        echo "Pre-caching Marker models for GPU build..."; \
        python3 -c "import os; os.environ['INFERENCE_RAM']='16'; from marker.converters.pdf import PdfConverter; from marker.models import create_model_dict; PdfConverter(artifact_dict=create_model_dict())"; \
    else \
        echo "Skipping Marker model caching for CPU-only build"; \
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
