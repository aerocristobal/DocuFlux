#!/bin/bash
# GPU Detection and Conditional Docker Build Script
# Detects GPU availability and builds appropriate worker image

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}DocuFlux - Conditional Build Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Function to detect GPU
detect_gpu() {
    echo -e "${YELLOW}Detecting GPU availability...${NC}"

    # Check if nvidia-smi is available
    if command -v nvidia-smi &> /dev/null; then
        # Check if nvidia-smi can successfully query GPU
        if nvidia-smi &> /dev/null; then
            echo -e "${GREEN}✓ NVIDIA GPU detected${NC}"
            nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
            return 0
        else
            echo -e "${YELLOW}⚠ nvidia-smi found but cannot access GPU${NC}"
            return 1
        fi
    else
        echo -e "${YELLOW}⚠ nvidia-smi not found${NC}"
        return 1
    fi
}

# Parse command line arguments
BUILD_MODE="${1:-auto}"
IMAGE_TAG="worker"

if [ "$BUILD_MODE" = "gpu" ]; then
    echo -e "${BLUE}Mode: Forced GPU build${NC}"
    BUILD_GPU=true
    IMAGE_TAG="worker:gpu"
elif [ "$BUILD_MODE" = "cpu" ]; then
    echo -e "${BLUE}Mode: Forced CPU build${NC}"
    BUILD_GPU=false
    IMAGE_TAG="worker:cpu"
elif [ "$BUILD_MODE" = "auto" ]; then
    echo -e "${BLUE}Mode: Auto-detect${NC}"
    if detect_gpu; then
        BUILD_GPU=true
        IMAGE_TAG="worker:gpu"
    else
        BUILD_GPU=false
        IMAGE_TAG="worker:cpu"
    fi
else
    echo -e "${RED}Error: Invalid build mode '${BUILD_MODE}'${NC}"
    echo "Usage: $0 [auto|gpu|cpu]"
    echo "  auto - Auto-detect GPU (default)"
    echo "  gpu  - Force GPU build"
    echo "  cpu  - Force CPU-only build"
    exit 1
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Build Configuration:${NC}"
echo -e "${GREEN}  BUILD_GPU: ${BUILD_GPU}${NC}"
echo -e "${GREEN}  Image Tag: ${IMAGE_TAG}${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Build the worker image
echo -e "${BLUE}Building worker image...${NC}"
docker build \
    --build-arg BUILD_GPU=${BUILD_GPU} \
    --tag ${IMAGE_TAG} \
    --file worker/Dockerfile \
    worker/

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}✓ Build successful!${NC}"
    echo -e "${GREEN}  Image: ${IMAGE_TAG}${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""

    # Show image size
    echo -e "${BLUE}Image details:${NC}"
    docker images ${IMAGE_TAG} --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"
    echo ""

    # Build recommendations
    if [ "$BUILD_GPU" = "true" ]; then
        echo -e "${YELLOW}To run with GPU profile:${NC}"
        echo "  docker-compose --profile gpu up"
    else
        echo -e "${YELLOW}To run with CPU profile:${NC}"
        echo "  docker-compose --profile cpu up"
    fi
    echo ""

else
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}✗ Build failed${NC}"
    echo -e "${RED}========================================${NC}"
    exit 1
fi
