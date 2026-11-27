# ─── Base Image ──────────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

# ─── Environment Configuration ───────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_THREAD_LIMIT=1 \
    PORT=8000

WORKDIR /app

# ─── System Dependencies ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# ─── Python Dependencies ─────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── Application Code ────────────────────────────────────────────────────────
COPY app ./app
COPY main.py .
USER 10001:10001

# ─── Expose Port ─────────────────────────────────────────────────────────────
EXPOSE 8000

# ─── Start FastAPI Server on Railway Dynamic Port ────────────────────────────
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
