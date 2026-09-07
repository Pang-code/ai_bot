FROM python:3.12-slim-bookworm

ARG MINERU_VERSION=3.4.1
ARG TORCH_VERSION=2.9.0
ARG TORCHVISION_VERSION=0.24.0

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        fontconfig \
        fonts-noto-cjk \
        fonts-noto-core \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# 安装 CPU 版 PyTorch，避免下载无用的 CUDA 运行时和占用额外镜像空间。
RUN python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==${TORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
    && python -m pip install "mineru[pipeline]==${MINERU_VERSION}"

WORKDIR /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl --fail --silent http://127.0.0.1:8000/health || exit 1

CMD ["mineru-api", "--host", "0.0.0.0", "--port", "8000"]
