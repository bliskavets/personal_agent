# ---------------------------------------------------------------------------
# ASR Microservice — Dockerfile
# Base: CUDA 12.3.1 + cuDNN 9 on Ubuntu 22.04 (matches design spec)
# ---------------------------------------------------------------------------
FROM nvidia/cuda:12.3.1-cudnn9-runtime-ubuntu22.04

# ---- System dependencies ---------------------------------------------------
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-dev \
        python3-pip \
        python3.11-venv \
        ffmpeg \
        libsndfile1 \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Make python3.11 the default python / pip
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
 && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# ---- Working directory ------------------------------------------------------
WORKDIR /app

# ---- Python dependencies ----------------------------------------------------
# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ---- Application source -----------------------------------------------------
COPY . .

# ---- Runtime configuration --------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    WHISPER_MODEL=large-v3 \
    WHISPER_DEVICE=cuda \
    WHISPER_COMPUTE_TYPE=float16

# Port exposed by uvicorn
EXPOSE 8000

# ---- Health check -----------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

# ---- Entry point ------------------------------------------------------------
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
