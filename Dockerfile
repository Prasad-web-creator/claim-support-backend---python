# ─── Base Image ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ─── Environment Configuration ───────────────────────────────────────────────
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# ─── System Dependencies ─────────────────────────────────────────────────────
# curl: Health checks on Railway
RUN apt-get update && apt-get install -y --no-install-recommends \
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
