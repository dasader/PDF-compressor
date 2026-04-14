#!/bin/bash
set -e

echo "=== PDF Compressor Worker 시작 ==="

# 환경 변수 출력
echo "📋 환경 설정:"
echo "  - REDIS_HOST: ${REDIS_HOST:-redis}"
echo "  - REDIS_PORT: ${REDIS_PORT:-6379}"
echo "  - API_URL: ${API_URL:-http://backend:8000}"
echo "  - LOG_LEVEL: ${LOG_LEVEL:-WARNING}"

# 환경변수 검증
if [ -z "$REDIS_HOST" ]; then
    REDIS_HOST="redis"
    echo "⚠️  REDIS_HOST이 설정되지 않았습니다. 기본값 사용: redis"
fi

if [ -z "$API_URL" ]; then
    API_URL="http://backend:8000"
    echo "⚠️  API_URL이 설정되지 않았습니다. 기본값 사용: http://backend:8000"
fi

export REDIS_HOST
export REDIS_PORT=${REDIS_PORT:-6379}
export API_URL
export LOG_LEVEL=${LOG_LEVEL:-WARNING}

echo "📤 최종 환경변수 확인:"
echo "  - REDIS_HOST=$REDIS_HOST"
echo "  - REDIS_PORT=$REDIS_PORT"
echo "  - API_URL=$API_URL"

# Redis 연결 확인
echo "[1/4] Redis 연결 확인..."
MAX_RETRIES=30
RETRY_COUNT=0

until python3 << PYTHON_SCRIPT
import redis
import sys
try:
    r = redis.Redis(
        host='$REDIS_HOST',
        port=${REDIS_PORT},
        socket_connect_timeout=5,
        decode_responses=True
    )
    r.ping()
    print("✅ Redis 연결 성공")
    sys.exit(0)
except Exception as e:
    print(f"❌ Redis 연결 실패: {e}")
    sys.exit(1)
PYTHON_SCRIPT
do
    RETRY_COUNT=$((RETRY_COUNT+1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "❌ Redis 연결 실패 (${MAX_RETRIES}회 시도)"
        exit 1
    fi
    if [ $((RETRY_COUNT % 5)) -eq 0 ]; then
        echo "⏳ Redis 대기 중... ($RETRY_COUNT/$MAX_RETRIES)"
    fi
    sleep 1
done

# 데이터베이스 초기화
echo "[2/4] 데이터베이스 테이블 확인 및 생성..."
python3 -m app.init_db

# Backend가 준비될 때까지 대기
echo "[3/4] Backend 서버 대기 중..."
MAX_RETRIES=60
RETRY_COUNT=0

until python3 << PYTHON_SCRIPT
import requests
import os
import sys

api_url = os.environ.get('API_URL', 'http://backend:8000')
print(f"🔗 Backend 확인 중: {api_url}/api/readyz")

try:
    response = requests.get(f'{api_url}/api/readyz', timeout=5)
    response.raise_for_status()
    print(f"✅ Backend 연결 성공 ({response.status_code})")
    sys.exit(0)
except requests.exceptions.ConnectionError as e:
    print(f"❌ Backend 연결 실패: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Backend 오류: {e}")
    sys.exit(1)
PYTHON_SCRIPT
do
    RETRY_COUNT=$((RETRY_COUNT+1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "❌ Backend 서버 시작 대기 시간 초과 (${MAX_RETRIES}회 시도)"
        echo "⚠️  Worker를 계속 시작하지만 작업 처리 시 오류가 발생할 수 있습니다"
        break
    fi
    if [ $((RETRY_COUNT % 10)) -eq 0 ]; then
        echo "⏳ Backend 서버 대기 중... ($RETRY_COUNT/$MAX_RETRIES)"
    fi
    sleep 1
done

if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
    echo "✅ Backend 서버 준비 완료"
fi

# Celery Worker 시작
echo "[4/4] Celery Worker 시작..."
echo "ℹ️  Worker 로그 레벨: ${LOG_LEVEL}"
echo "ℹ️  이제부터 Celery Worker 로그가 표시됩니다..."
echo ""

exec celery -A app.workers.celery_app worker \
    --beat \
    --schedule=/data/celerybeat-schedule \
    --loglevel=info \
    --concurrency=${WORKER_CONCURRENCY:-1} \
    -n worker@%h


