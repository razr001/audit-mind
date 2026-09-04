# MinerU 3.4.2 is pinned so rebuilding on another host does not silently
# change parsing output or API behavior.
FROM vllm/vllm-openai:v0.21.0

# OpenCV needs libgl; Noto fonts keep Chinese text readable in generated files.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        fontconfig \
        fonts-noto-cjk \
        fonts-noto-core \
        libgl1 && \
    fc-cache -fv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
        "mineru[core]==3.4.2" \
        --break-system-packages

# Download every MinerU model while building. The runtime containers therefore
# do not depend on an external model registry and can start predictably.
ARG MINERU_MODEL_DOWNLOAD_SOURCE=huggingface
RUN mineru-models-download \
        -s "${MINERU_MODEL_DOWNLOAD_SOURCE}" \
        -m all

ENV MINERU_MODEL_SOURCE=local

ENTRYPOINT ["/bin/bash", "-c", "exec \"$@\"", "--"]
