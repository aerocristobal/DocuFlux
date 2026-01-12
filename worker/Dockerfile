FROM nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

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

# Install PyTorch with CUDA 11.8 support
RUN pip3 install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy worker code
COPY . .

# Pre-cache Marker models during build
RUN python3 -c "import os; os.environ['INFERENCE_RAM']='16'; from marker.converters.pdf import PdfConverter; from marker.models import create_model_dict; PdfConverter(artifact_dict=create_model_dict())"

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Set entrypoint
ENTRYPOINT ["./entrypoint.sh"]

# Run celery worker (command overridden in docker-compose.yml)
CMD ["celery", "-A", "tasks", "worker", "--loglevel=info", "--pool=gevent", "--concurrency=10"]
