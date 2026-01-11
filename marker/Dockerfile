FROM nvcr.io/nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04

RUN apt-get update \
    && apt-get install -y git git-lfs python3 python3-pip curl \
    && curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | bash \
    && git lfs install \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

RUN apt-get update \
    && apt-get install -y libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/adithya-s-k/marker-api \
    && cd marker-api && pip3 install --no-cache-dir -e .

# Patch surya to fix 'sdpa' KeyError by forcing eager attention
RUN sed -i '/model = OrderVisionEncoderDecoderModel.from_pretrained/i \    config._attn_implementation = "eager"' /usr/local/lib/python3.10/dist-packages/surya/model/ordering/model.py

# Patch server.py to mount Gradio at /gradio to avoid shadowing API routes
RUN sed -i 's/path=""/path="\/gradio"/' /marker-api/server.py

WORKDIR /marker-api

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
