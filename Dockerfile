# ─── Base Image ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ─── Environment Configuration ───────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_LOG_LEVEL=ERROR \
    PORT=8000

WORKDIR /app

# ─── System Dependencies for PaddleOCR, PaddlePaddle, and OpenCV ─────────────
# libgomp1: Required by PaddlePaddle CPU engine for OpenMP multithreading
# libgl1, libglib2.0-0: Required by OpenCV for image processing
# libsm6, libxext6, libxrender-dev: Required by PDF and rendering libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ─── Python Dependencies ─────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ─── Application Code ────────────────────────────────────────────────────────
COPY . .

# ─── Expose Port ─────────────────────────────────────────────────────────────
EXPOSE 8000

# ─── Start FastAPI Server on Railway Dynamic Port ────────────────────────────
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
