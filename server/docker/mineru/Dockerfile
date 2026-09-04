# MinerU 的 pipeline 后端支持纯 CPU。CPU 镜像不继承 vLLM/CUDA，
# 用于没有 NVIDIA GPU 的 Linux/WSL2 开发机和轻量验证环境。
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# LibreOffice 负责 Office 文档转换；OpenCV 和中文渲染需要下面的
# 图形库与字体。curl 仅供容器健康检查使用。
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        fontconfig \
        fonts-noto-cjk \
        fonts-noto-core \
        libgl1 \
        libglib2.0-0 \
        libreoffice && \
    fc-cache -fv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 先从 PyTorch 官方 CPU 仓库安装匹配版本，避免 PyPI 的 Linux wheel
# 把 CUDA 运行时一并拉进纯 CPU 镜像。
RUN python -m pip install \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.6.0" \
        "torchvision==0.21.0" && \
    python -m pip install "mineru[pipeline]==3.4.2"

# CPU 模式只需要 pipeline 模型，不下载 VLM/vLLM 模型，减少镜像体积。
ARG MINERU_MODEL_DOWNLOAD_SOURCE=huggingface
RUN mineru-models-download \
        --source "${MINERU_MODEL_DOWNLOAD_SOURCE}" \
        --model_type pipeline

# MinerU 3.4.2 的内置 Paddle OCR 会直接导入 six，但 pipeline extra
# 未声明该运行时依赖。
RUN python -m pip install "six==1.17.0"

ENV MINERU_MODEL_SOURCE=local \
    CUDA_VISIBLE_DEVICES=""

ENTRYPOINT ["/bin/bash", "-c", "exec \"$@\"", "--"]
