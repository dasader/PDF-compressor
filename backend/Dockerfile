# ---------- builder stage ----------
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libmagic-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/home/appuser/.local/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript \
    qpdf \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser
WORKDIR /app

COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

COPY --chown=appuser:appuser ./app ./app
COPY --chown=appuser:appuser entrypoint.sh /entrypoint.sh
COPY --chown=appuser:appuser worker-entrypoint.sh /worker-entrypoint.sh
USER root
RUN chmod +x /entrypoint.sh /worker-entrypoint.sh && \
    mkdir -p /data/uploads /data/results /data/temp && \
    chown -R appuser:appuser /data
USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/healthz')"

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
