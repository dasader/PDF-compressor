#!/bin/bash
set -e

echo "=== PDF Compressor Worker 시작 ==="

# redis/backend 기동 대기는 compose의 depends_on(condition: service_healthy)이 보장한다

echo "[1/2] 데이터베이스 테이블 확인 및 생성..."
python3 -m app.init_db

echo "[2/2] Celery Worker 시작 (Beat 내장)..."
exec celery -A app.workers.celery_app worker \
    --beat \
    --schedule=/data/celerybeat-schedule \
    --loglevel="${LOG_LEVEL:-info}" \
    --concurrency="${WORKER_CONCURRENCY:-1}" \
    -n worker@%h
