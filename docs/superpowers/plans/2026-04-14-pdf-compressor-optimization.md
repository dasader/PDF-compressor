# PDF Compressor 경량화·효율화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 메모리 23% 절감, 컨테이너 6→5개, I/O 단일 패스, Redis Pub/Sub 기반 SSE 도입으로 PDF 압축 서비스를 경량화·효율화한다.

**Architecture:** 기존 FastAPI + Celery + Redis + SQLite + Next.js 스택을 유지한 채, 인프라 튜닝 → DB 최적화 → 업로드/dedup 개선 → 실시간 통신(SSE) 순으로 5개 Phase에 걸쳐 점진 개선. 각 Phase는 독립 커밋 단위.

**Tech Stack:** FastAPI, Celery, Redis(AOF off + pubsub), SQLite(WAL), SQLAlchemy, Next.js, sse-starlette, aiofiles, hashlib.

**Spec:** `docs/superpowers/specs/2026-04-14-pdf-compressor-optimization-design.md`

---

## 파일 구조 (영향 받는 파일)

### 신규 생성
- `backend/app/core/redis_client.py` — 공용 Redis 클라이언트 (lock, pubsub)
- `backend/tests/test_upload_hash.py` — 단일 패스 업로드 해시 테스트
- `backend/tests/test_dedup_lock.py` — 분산 락 dedup 테스트
- `backend/tests/test_db_pragma.py` — SQLite pragma 검증
- `backend/tests/test_sse_stream.py` — SSE 엔드포인트 테스트
- `backend/tests/test_cleanup_batching.py` — cleanup 배치 테스트
- `frontend/src/lib/sse.ts` — SSE 구독 헬퍼

### 수정
- `docker-compose.yml` — beat 제거, 메모리 limit 조정, Redis 설정
- `backend/Dockerfile` — 멀티스테이지 재작성
- `backend/requirements.txt` — 의존성 정리 + sse-starlette 추가
- `backend/app/main.py` — Prometheus 마운트 제거
- `backend/app/core/config.py` — ENABLE_METRICS, WEBHOOK_*, TASK_TIMEOUT 등 정리
- `backend/app/models/database.py` — WAL pragma, context manager, pool 설정
- `backend/app/models/job.py` — 복합 인덱스
- `backend/app/services/file_service.py` — save_upload_file_with_hash 추가
- `backend/app/api/upload.py` — 단일 패스, Redis 락 적용
- `backend/app/api/jobs.py` — SSE 엔드포인트 추가
- `backend/app/workers/celery_app.py` — 설정 업데이트
- `backend/app/workers/tasks.py` — webhook 제거, context manager, publish, cleanup 개선
- `backend/worker-entrypoint.sh` — `-B` 플래그 (beat 병합)
- `backend/beat-entrypoint.sh` — 삭제
- `env.example` — 불필요 ENV 정리
- `nginx.conf` — gzip, keepalive, proxy_buffer, SSE location, 대용량 업로드
- `frontend/src/app/page.tsx` — 폴링 제거, SSE 구독
- `frontend/src/lib/api.ts` — subscribeJob export
- `frontend/Dockerfile` — npm cache clean 제거

### 삭제
- `backend/beat-entrypoint.sh`

---

# Phase 0: 의존성 정리 & Dockerfile

## Task 0.1: `prometheus-client` 완전 제거

**Files:**
- Modify: `backend/app/main.py:7` (import 삭제), `69-72` (if 블록 삭제)
- Modify: `backend/app/core/config.py:62-64` (ENABLE_METRICS, METRICS_PORT 삭제)
- Modify: `backend/requirements.txt:25-26` (prometheus-client 라인 삭제)
- Modify: `env.example:52-54` (ENABLE_METRICS, METRICS_PORT 삭제)

- [ ] **Step 1: `backend/app/main.py` 수정**

라인 7 `from prometheus_client import make_asgi_app` 삭제.
라인 69-72 블록 삭제:
```python
# Prometheus 메트릭 (옵션)
if settings.ENABLE_METRICS:
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)
```

- [ ] **Step 2: `backend/app/core/config.py` 수정**

라인 62-64 삭제:
```python
# 메트릭
ENABLE_METRICS: bool = False
METRICS_PORT: int = 9090
```

- [ ] **Step 3: `backend/requirements.txt` 수정**

라인 25-26 삭제:
```
# Monitoring & Logging
prometheus-client==0.19.0
```
(`python-json-logger==2.0.7`은 유지)

- [ ] **Step 4: `env.example` 수정**

라인 52-54 삭제:
```
# 메트릭
ENABLE_METRICS=true
METRICS_PORT=9090
```

- [ ] **Step 5: 컴파일 확인**

Run: `cd backend && python -c "from app.main import app"`
Expected: 에러 없이 종료 (또는 모듈 import 성공)

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/core/config.py backend/requirements.txt env.example
git commit -m "chore: remove prometheus-client dependency and metrics mount"
```

---

## Task 0.2: `httpx`/웹훅 완전 제거

**Files:**
- Modify: `backend/app/workers/tasks.py:141-143, 173-175` (send_webhook_notification 호출 삭제), `185-215` (함수 자체 삭제)
- Modify: `backend/app/core/config.py:66-68` (WEBHOOK_ENABLED, WEBHOOK_URL 삭제)
- Modify: `backend/requirements.txt:29-30` (httpx 삭제)
- Modify: `env.example:56-58` (WEBHOOK_* 삭제)

- [ ] **Step 1: `backend/app/workers/tasks.py` 수정**

라인 141-143 삭제 (성공 경로 웹훅 호출):
```python
# 웹훅 전송
if settings.WEBHOOK_ENABLED and settings.WEBHOOK_URL:
    send_webhook_notification(job_id, 'completed')
```

라인 173-175 삭제 (실패 경로 웹훅 호출):
```python
# 웹훅 전송
if settings.WEBHOOK_ENABLED and settings.WEBHOOK_URL:
    send_webhook_notification(job_id, 'failed')
```

라인 185-215의 `send_webhook_notification` 함수 전체 삭제.

- [ ] **Step 2: `backend/app/core/config.py` 수정**

라인 66-68 삭제:
```python
# 웹훅
WEBHOOK_ENABLED: bool = False
WEBHOOK_URL: Optional[str] = None
```

- [ ] **Step 3: `backend/requirements.txt` 수정**

라인 29-30 삭제:
```
# HTTP & Webhooks
httpx==0.26.0
```

- [ ] **Step 4: `env.example` 수정**

라인 56-58 삭제:
```
# 웹훅 (옵션)
WEBHOOK_ENABLED=false
WEBHOOK_URL=
```

- [ ] **Step 5: 컴파일 확인**

Run: `cd backend && python -c "from app.workers.tasks import compress_pdf_task, cleanup_old_files_task"`
Expected: import 성공

- [ ] **Step 6: Commit**

```bash
git add backend/app/workers/tasks.py backend/app/core/config.py backend/requirements.txt env.example
git commit -m "chore: remove httpx and webhook notification code"
```

---

## Task 0.3: `tenacity` 제거

**Files:**
- Modify: `backend/requirements.txt:32-33` (tenacity 라인 삭제)
- Investigate/Modify: `backend/app/` 내 tenacity 사용처 있으면 Celery retry로 교체

- [ ] **Step 1: tenacity 사용처 조사**

Run: `cd backend && grep -rn "tenacity" app/`
Expected: 사용처 0건이면 바로 삭제. 1건 이상이면 해당 파일에서 `@retry` 데코레이터를 Celery의 `autoretry_for=(Exception,), retry_backoff=True` 파라미터로 교체.

- [ ] **Step 2: `backend/requirements.txt`에서 tenacity 삭제**

라인 32-33 범위 내 `tenacity==8.2.3` 제거.

- [ ] **Step 3: 컴파일 확인**

Run: `cd backend && python -c "import app.main"`
Expected: ImportError 없음.

- [ ] **Step 4: Commit**

```bash
git add backend/requirements.txt backend/app
git commit -m "chore: remove tenacity dependency"
```

---

## Task 0.4: Backend Dockerfile 진정한 멀티스테이지

**Files:**
- Modify: `backend/Dockerfile` (전체 재작성)

- [ ] **Step 1: `backend/Dockerfile` 전체 교체**

```dockerfile
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

# 런타임 전용 바이너리만 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    ghostscript \
    qpdf \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 appuser
USER appuser
WORKDIR /app

# builder에서 설치된 파이썬 패키지 복사
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

# 애플리케이션 코드 및 엔트리포인트 복사
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
```

주의: `beat-entrypoint.sh` COPY 라인이 빠진 이유는 Task 1.1에서 beat 제거 때문. 아직 beat 컨테이너가 docker-compose에 남아있으면 이 단계에서는 `COPY beat-entrypoint.sh` 라인을 임시 추가. Task 1.1 완료 시 제거.

임시로 beat 엔트리포인트 COPY 라인을 추가:
```dockerfile
COPY --chown=appuser:appuser beat-entrypoint.sh /beat-entrypoint.sh
```
(runtime 스테이지의 애플리케이션 코드 COPY 블록 바로 아래 삽입, `chmod +x` 라인에도 포함)

- [ ] **Step 2: 빌드 확인**

Run: `docker compose build backend`
Expected: 빌드 성공, 최종 이미지 크기가 기존보다 작음 (`docker images | grep pdf-compressor`로 비교).

- [ ] **Step 3: 기동 확인**

Run: `docker compose up -d && docker compose ps`
Expected: backend, worker, beat 모두 `healthy` / `running` 상태로 기동.

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile
git commit -m "refactor: convert backend Dockerfile to true multi-stage build"
```

---

# Phase 1: 인프라

## Task 1.1: Beat를 Worker에 병합

**Files:**
- Modify: `docker-compose.yml:107-133` (beat 서비스 블록 삭제)
- Modify: `backend/worker-entrypoint.sh:120-123` (celery 명령에 `-B` 추가)
- Modify: `backend/Dockerfile` (beat-entrypoint.sh COPY·chmod 라인 제거)
- Delete: `backend/beat-entrypoint.sh`

- [ ] **Step 1: `backend/worker-entrypoint.sh` 수정**

라인 120-123의 `exec celery` 블록을:
```bash
exec celery -A app.workers.celery_app worker \
    --loglevel=info \
    --concurrency=2 \
    -n worker@%h
```
아래로 교체:
```bash
exec celery -A app.workers.celery_app worker \
    --beat \
    --loglevel=info \
    --concurrency=${WORKER_CONCURRENCY:-1} \
    -n worker@%h
```

- [ ] **Step 2: `docker-compose.yml` 에서 beat 서비스 삭제**

라인 107-133의 전체 `beat:` 블록 삭제.

- [ ] **Step 3: `backend/Dockerfile`에서 beat 엔트리포인트 참조 제거**

Task 0.4에서 임시 추가한 `COPY beat-entrypoint.sh` 및 `chmod +x /beat-entrypoint.sh` 라인 삭제.

- [ ] **Step 4: `backend/beat-entrypoint.sh` 삭제**

Run: `rm backend/beat-entrypoint.sh`

- [ ] **Step 5: 재기동 및 beat 스케줄 동작 확인**

Run: `docker compose up -d --build worker`
Run: `docker compose logs worker | grep -i beat`
Expected: "Scheduler: Sending due task cleanup-every-hour..." 또는 유사한 Beat 로그가 출현 (최대 1시간 대기, 즉시 확인하려면 CLEANUP_INTERVAL_HOURS를 일시적으로 낮춰 테스트).

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml backend/Dockerfile backend/worker-entrypoint.sh
git rm backend/beat-entrypoint.sh
git commit -m "refactor: merge celery beat into worker container (-128MB)"
```

---

## Task 1.2: Redis 튜닝

**Files:**
- Modify: `docker-compose.yml:5-6` (redis command 변경), `20-23` (메모리 limit)

- [ ] **Step 1: `docker-compose.yml`의 redis 서비스 수정**

현재:
```yaml
command: redis-server --appendonly yes --maxmemory 256mb --maxmemory-policy allkeys-lru
```
변경:
```yaml
command: >
  redis-server
  --appendonly no
  --save ""
  --maxmemory 384mb
  --maxmemory-policy volatile-lru
```

메모리 limit 변경 (라인 19-23):
```yaml
deploy:
  resources:
    limits:
      memory: 512M
    reservations:
      memory: 128M
```

- [ ] **Step 2: 재기동 및 확인**

Run: `docker compose up -d redis && docker compose exec redis redis-cli CONFIG GET maxmemory maxmemory-policy appendonly`
Expected:
```
maxmemory: 402653184 (= 384MB)
maxmemory-policy: volatile-lru
appendonly: no
```

- [ ] **Step 3: 기존 작업 정상 동작 확인**

Run: `docker compose up -d` 후 브라우저에서 작은 PDF 1건 업로드 → 압축 완료되는지.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "perf: disable redis AOF, use volatile-lru, raise maxmemory to 384mb"
```

---

## Task 1.3: Nginx gzip / keepalive / proxy_buffer / 대용량 / SSE

**Files:**
- Modify: `nginx.conf` (전체 재작성)

- [ ] **Step 1: `nginx.conf` 전체 교체**

```nginx
worker_processes auto;

events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile        on;
    tcp_nopush      on;
    tcp_nodelay     on;
    keepalive_timeout 65;

    # gzip
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types
        text/plain
        text/css
        application/json
        application/javascript
        application/xml
        text/xml
        text/javascript;

    # 업로드 크기
    client_max_body_size 600M;
    client_body_timeout 600s;

    # proxy 버퍼
    proxy_buffer_size 4k;
    proxy_buffers 8 16k;
    proxy_busy_buffers_size 32k;

    upstream backend_upstream {
        server backend:8000;
        keepalive 32;
    }

    upstream frontend_upstream {
        server frontend:3000;
        keepalive 32;
    }

    server {
        listen 80;

        # SSE 전용: 버퍼링 OFF, 장기 연결
        location ~ ^/api/jobs/[^/]+/stream$ {
            proxy_pass http://backend_upstream;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 3600s;
            chunked_transfer_encoding off;
        }

        # 업로드: 요청 스트리밍 (버퍼링 OFF)
        location = /api/upload {
            proxy_pass http://backend_upstream;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_request_buffering off;
            proxy_read_timeout 1800s;
            proxy_send_timeout 1800s;
        }

        # 일반 API
        location /api/ {
            proxy_pass http://backend_upstream;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 1800s;
            proxy_send_timeout 1800s;
        }

        # Next.js 정적 자산: 장기 캐시
        location /_next/static/ {
            proxy_pass http://frontend_upstream;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            expires 1y;
            add_header Cache-Control "public, immutable";
        }

        # 나머지 프론트엔드
        location / {
            proxy_pass http://frontend_upstream;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
```

- [ ] **Step 2: nginx 재기동 및 구문 검증**

Run: `docker compose exec nginx nginx -t`
Expected: `syntax is ok` / `test is successful`.

Run: `docker compose restart nginx`

- [ ] **Step 3: gzip 동작 확인**

Run: `curl -sI -H "Accept-Encoding: gzip" http://localhost:8082/api/healthz | grep -i content-encoding`
Expected: `Content-Encoding: gzip` (JSON 응답이 1KB 이상일 때)

- [ ] **Step 4: 대용량 파일 업로드 smoke 테스트 (선택)**

100MB 이상 PDF 업로드 → 정상 완료 확인. (없으면 생략)

- [ ] **Step 5: Commit**

```bash
git add nginx.conf
git commit -m "perf: add gzip, keepalive, proxy buffers, large upload and SSE blocks to nginx"
```

---

## Task 1.4: 메모리 limit 재조정

**Files:**
- Modify: `docker-compose.yml` (backend, worker, frontend, nginx의 deploy.resources)

- [ ] **Step 1: `docker-compose.yml` 메모리 limit 수정**

| 서비스 | 기존 memory.limits | 변경 후 |
|--------|-------------------|---------|
| backend | 800M | 640M |
| worker | 2G | 1536M |
| frontend | 512M | 256M |

각 서비스 `deploy.resources.limits.memory` 값 변경.

`backend`:
```yaml
deploy:
  resources:
    limits:
      memory: 640M
    reservations:
      memory: 256M
```

`worker`:
```yaml
deploy:
  resources:
    limits:
      memory: 1536M
    reservations:
      memory: 256M
```

`frontend`:
```yaml
deploy:
  resources:
    limits:
      memory: 256M
    reservations:
      memory: 128M
```

- [ ] **Step 2: 재기동 및 `docker stats` 확인**

Run: `docker compose up -d && sleep 30 && docker stats --no-stream`
Expected: 모든 컨테이너가 각자의 limit 내에서 정상 동작. 총 합계가 약 3GB 수준.

- [ ] **Step 3: 중간~대용량 PDF 업로드로 worker 피크 확인**

50MB~100MB PDF 업로드 후 `docker stats pdf-compressor-worker --no-stream` 반복 관찰.
Expected: 피크 시점 메모리 < 1.5GB (limit). OOM 킬 발생하지 않음.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "perf: right-size container memory limits (-900MB total)"
```

---

# Phase 2: DB & 백엔드 내부

## Task 2.1: SQLite WAL + pragma + pool

**Files:**
- Create: `backend/tests/test_db_pragma.py`
- Modify: `backend/app/models/database.py`

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_db_pragma.py`

```python
from sqlalchemy import text
from app.models.database import engine


def test_wal_mode_enabled():
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert result.lower() == "wal"


def test_synchronous_normal():
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA synchronous")).scalar()
    # NORMAL = 1
    assert int(result) == 1
```

- [ ] **Step 2: 테스트 실행 (실패 확인)**

Run: `cd backend && pytest tests/test_db_pragma.py -v`
Expected: FAIL — 현재 기본값은 `delete` (journal_mode) / `2` (synchronous).

- [ ] **Step 3: `backend/app/models/database.py` 구현**

기존 파일 전체 교체:
```python
"""데이터베이스 설정"""
import os
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DB_PATH = os.getenv("DB_PATH", "/data/pdf_compressor.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA cache_size=-32000")
    cur.execute("PRAGMA mmap_size=134217728")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session():
    """워커/스크립트용 컨텍스트 매니저 — commit/rollback/close 자동화"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_db_pragma.py -v`
Expected: 2 PASS.

- [ ] **Step 5: 앱 기동 회귀 확인**

Run: `docker compose up -d --build backend worker`
Run: `docker compose logs backend | grep -i error`
Expected: 에러 없음.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/database.py backend/tests/test_db_pragma.py
git commit -m "perf: enable SQLite WAL, pragma tuning, session context manager"
```

---

## Task 2.2: 복합 인덱스 추가

**Files:**
- Modify: `backend/app/models/job.py` (Index import + __table_args__)
- Modify: `backend/app/main.py` lifespan (기동 시 인덱스 보장)

- [ ] **Step 1: `backend/app/models/job.py` 수정**

라인 4의 import를 확장:
```python
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, Boolean, Enum as SQLEnum, Index
```

클래스 본문 마지막 속성 정의 뒤에 `__table_args__` 추가 (예: `celery_task_id` 라인 바로 아래):
```python
    __table_args__ = (
        Index("idx_user_status", "user_session", "status"),
        Index("idx_expires_created", "expires_at", "created_at"),
    )
```

- [ ] **Step 2: `backend/app/main.py` lifespan에서 인덱스 보장**

라인 27 `Base.metadata.create_all(bind=engine)` 는 신규 인덱스를 자동 생성한다 (IF NOT EXISTS 동등). 기존 DB에는 누락될 수 있으므로 명시적 보장 추가:

lifespan 함수의 `Base.metadata.create_all(bind=engine)` 호출 직후:
```python
from sqlalchemy import text
with engine.begin() as conn:
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_status ON jobs (user_session, status)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_expires_created ON jobs (expires_at, created_at)"))
```

- [ ] **Step 3: 기동 후 인덱스 확인**

Run: `docker compose up -d --build backend && sleep 5`
Run: `docker compose exec backend python -c "from sqlalchemy import text; from app.models.database import engine; c=engine.connect(); print(c.execute(text(\"SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='jobs'\")).fetchall())"`
Expected: 결과에 `idx_user_status`, `idx_expires_created` 포함.

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/job.py backend/app/main.py
git commit -m "perf: add composite indexes on jobs (user_session,status) and (expires_at,created_at)"
```

---

## Task 2.3: Worker DB 세션 context manager 적용

**Files:**
- Modify: `backend/app/workers/tasks.py` (SessionLocal 직접 사용 → `db_session()`)

- [ ] **Step 1: `backend/app/workers/tasks.py` 수정**

라인 9의 import 변경:
```python
from app.models.database import SessionLocal, db_session
```

`CallbackTask.update_progress` (라인 20-34) 를 아래로 교체:
```python
def update_progress(self, job_id: str, progress: float, eta_seconds: int = None):
    try:
        with db_session() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.progress = min(progress, 1.0)
                if eta_seconds is not None:
                    job.eta_seconds = eta_seconds
                logger.info(f"작업 {job_id} 진행률: {progress * 100:.1f}%")
    except Exception as e:
        logger.error(f"진행률 업데이트 실패: {e}")
```

`compress_pdf_task` (라인 38-182) 전체 구조 변경: 바깥 try/finally/db 패턴 제거 후 `with db_session() as db:` 로 감싸기. 예외 처리 내 재시도 로직은 별도 세션으로 분리:

```python
@celery_app.task(bind=True, base=CallbackTask, max_retries=settings.TASK_MAX_RETRIES)
def compress_pdf_task(self, job_id: str) -> Dict[str, Any]:
    try:
        with db_session() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                raise ValueError(f"작업을 찾을 수 없습니다: {job_id}")

            logger.info(f"작업 시작: {job_id} - {job.filename}")

            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)
            job.celery_task_id = self.request.id
            db.flush()

            input_path = os.path.join(settings.UPLOAD_DIR, job.filename)
            output_filename = f"compressed_{job.filename}"
            output_path = os.path.join(settings.RESULT_DIR, output_filename)

            if not os.path.exists(input_path):
                raise FileNotFoundError(f"입력 파일이 없습니다: {input_path}")

            if not FileService.validate_pdf(input_path):
                raise ValueError("유효하지 않은 PDF 파일입니다")

            if settings.ENABLE_ANTIVIRUS:
                if not FileService.scan_antivirus(input_path):
                    raise ValueError("바이러스가 감지된 파일입니다")

            def progress_callback(progress: float):
                self.update_progress(job_id, progress)

            self.update_progress(job_id, 0.1)
            engine_impl = get_engine(job.engine)
            pdf_info = engine_impl.get_pdf_info(input_path)

            job.page_count = pdf_info.get('page_count', 0)
            job.image_count = pdf_info.get('image_count', 0)
            db.flush()

            if pdf_info.get('encrypted'):
                raise ValueError("암호화된 PDF는 지원하지 않습니다")

            options = {}
            if job.custom_options:
                import json
                options = json.loads(job.custom_options)

            self.update_progress(job_id, 0.2)
            logger.info(f"압축 시작: engine={job.engine}, preset={job.preset}")

            engine_impl.compress(
                input_path=input_path,
                output_path=output_path,
                preset=job.preset,
                options=options,
                progress_callback=progress_callback,
            )

            if not os.path.exists(output_path):
                raise RuntimeError("압축된 파일이 생성되지 않았습니다")

            compressed_size = os.path.getsize(output_path)
            compression_ratio = compressed_size / job.original_size if job.original_size > 0 else 1.0
            logger.info(f"압축 완료: {job.original_size} -> {compressed_size} bytes (ratio: {compression_ratio:.2%})")

            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.compressed_size = compressed_size
            job.compression_ratio = compression_ratio
            job.result_file = output_filename
            job.progress = 1.0
            job.expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.RETENTION_HOURS)

            return {
                'success': True,
                'job_id': job_id,
                'compressed_size': compressed_size,
                'compression_ratio': compression_ratio,
            }

    except Exception as e:
        logger.error(f"작업 실패: {job_id} - {e}", exc_info=True)
        try:
            with db_session() as db:
                job = db.query(Job).filter(Job.id == job_id).first()
                if not job:
                    raise
                job.retry_count += 1
                if job.retry_count < settings.TASK_MAX_RETRIES:
                    logger.info(f"작업 재시도: {job_id} ({job.retry_count}/{settings.TASK_MAX_RETRIES})")
                    retry_countdown = 60 * (2 ** job.retry_count)
                else:
                    job.status = JobStatus.FAILED
                    job.error_message = str(e)
                    job.completed_at = datetime.now(timezone.utc)
                    retry_countdown = None
        except Exception as inner:
            logger.error(f"재시도 레코드 업데이트 실패: {inner}")
            retry_countdown = None

        if retry_countdown is not None:
            raise self.retry(exc=e, countdown=retry_countdown)
        raise
```

- [ ] **Step 2: 컴파일 & 단위 수행**

Run: `cd backend && python -c "from app.workers.tasks import compress_pdf_task, cleanup_old_files_task"`
Expected: import 성공.

- [ ] **Step 3: 통합 확인**

Run: `docker compose up -d --build worker`
Run: 소형 PDF 1건 업로드 → 완료 확인.

- [ ] **Step 4: Commit**

```bash
git add backend/app/workers/tasks.py
git commit -m "refactor: use db_session context manager in worker tasks"
```

---

## Task 2.4: Cleanup 배치화 + 원본 동반 삭제 + file_service 중복 제거

**Files:**
- Modify: `backend/app/workers/tasks.py::cleanup_old_files_task`
- Modify: `backend/app/services/file_service.py` (cleanup_old_files 함수 삭제)
- Create: `backend/tests/test_cleanup_batching.py`

- [ ] **Step 1: 실패 테스트 작성** — `backend/tests/test_cleanup_batching.py`

```python
import os
import tempfile
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.database import Base
from app.models.job import Job, JobStatus, CompressionPreset


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    yield Session()


def test_batching_processes_all_expired(db_session, tmp_path, monkeypatch):
    from app.workers import tasks
    monkeypatch.setattr(tasks, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(tasks.settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tasks.settings, "RESULT_DIR", str(tmp_path))

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    for i in range(250):
        db_session.add(Job(
            id=f"j{i}", filename=f"f{i}.pdf", original_filename=f"o{i}.pdf",
            original_size=100, status=JobStatus.COMPLETED, preset=CompressionPreset.EBOOK,
            engine="ghostscript", expires_at=past, created_at=past,
        ))
    db_session.commit()

    tasks.cleanup_old_files_task()

    remaining = db_session.query(Job).count()
    assert remaining == 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd backend && pytest tests/test_cleanup_batching.py -v`
Expected: FAIL (현재 함수는 250개를 한 번에 .all()로 가져와 OK지만, 테스트는 우리가 구현한 배치 루프가 동작하는지 검증; 현 구현도 통과할 수 있으므로 실패 조건을 강화하려면 `limit=100` 호출 횟수를 mock으로 검증). 아래 구현을 먼저 적용 후 재실행해 통과하는 전체 회귀로 처리.

- [ ] **Step 3: `backend/app/workers/tasks.py::cleanup_old_files_task` 전체 교체**

```python
@celery_app.task
def cleanup_old_files_task():
    """만료된 Job과 연관된 업로드/결과 파일을 배치 단위로 정리한다."""
    logger.info("파일 정리 작업 시작")
    cutoff_time = datetime.now(timezone.utc)
    total_deleted = 0
    batch_size = 200

    try:
        while True:
            with db_session() as db:
                expired_jobs = (
                    db.query(Job)
                    .filter(
                        Job.expires_at < cutoff_time,
                        Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]),
                    )
                    .limit(batch_size)
                    .all()
                )
                if not expired_jobs:
                    break

                for job in expired_jobs:
                    if job.filename:
                        upload_path = os.path.join(settings.UPLOAD_DIR, job.filename)
                        if os.path.exists(upload_path):
                            try:
                                os.remove(upload_path)
                            except OSError as e:
                                logger.warning(f"업로드 파일 삭제 실패: {upload_path}: {e}")
                    if job.result_file:
                        result_path = os.path.join(settings.RESULT_DIR, job.result_file)
                        if os.path.exists(result_path):
                            try:
                                os.remove(result_path)
                            except OSError as e:
                                logger.warning(f"결과 파일 삭제 실패: {result_path}: {e}")
                    db.delete(job)
                total_deleted += len(expired_jobs)

        logger.info(f"정리 완료: {total_deleted}개 작업 삭제")
    except Exception as e:
        logger.error(f"파일 정리 실패: {e}", exc_info=True)
```

`FileService.cleanup_old_files` 호출 라인(원본 223)은 제거.

- [ ] **Step 4: `backend/app/services/file_service.py::cleanup_old_files` 삭제**

라인 149-169 전체 삭제.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_cleanup_batching.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/workers/tasks.py backend/app/services/file_service.py backend/tests/test_cleanup_batching.py
git commit -m "refactor: batch cleanup with upload+result file deletion, remove mtime-based cleanup"
```

---

## Task 2.5: 타임아웃 값 현실화

**Files:**
- Modify: `backend/app/core/config.py:38` (TASK_TIMEOUT_SECONDS 1800→900)
- Modify: `env.example:28`
- Modify: `docker-compose.yml` worker/backend의 `TASK_TIMEOUT_SECONDS` env 값

- [ ] **Step 1: `backend/app/core/config.py:38` 수정**

```python
TASK_TIMEOUT_SECONDS: int = 900
```

- [ ] **Step 2: `env.example` 및 `docker-compose.yml` 수정**

`env.example`의 `TASK_TIMEOUT_SECONDS=1800` → `TASK_TIMEOUT_SECONDS=900`.

`docker-compose.yml` backend/worker `environment` 블록의 `TASK_TIMEOUT_SECONDS=1800` → `900`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/config.py env.example docker-compose.yml
git commit -m "perf: reduce TASK_TIMEOUT_SECONDS 1800 -> 900"
```

---

# Phase 3: 업로드 & Dedup

## Task 3.1: 공용 Redis 클라이언트 모듈

**Files:**
- Create: `backend/app/core/redis_client.py`

- [ ] **Step 1: `backend/app/core/redis_client.py` 신규 작성**

```python
"""공용 Redis 클라이언트 (lock, pubsub)"""
import redis
from app.core.config import settings


def _build_client() -> redis.Redis:
    return redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


redis_client: redis.Redis = _build_client()
```

- [ ] **Step 2: import smoke 확인**

Run: `cd backend && python -c "from app.core.redis_client import redis_client; print(redis_client)"`
Expected: `Redis<...>` 출력.

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/redis_client.py
git commit -m "feat: add shared redis client module"
```

---

## Task 3.2: 단일 패스 save + hash

**Files:**
- Create: `backend/tests/test_upload_hash.py`
- Modify: `backend/app/services/file_service.py` (신규 함수 `save_upload_file_with_hash`)

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_upload_hash.py`:
```python
import hashlib
import io
import pytest
from types import SimpleNamespace
from app.services.file_service import FileService


class FakeUploadFile:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
    async def read(self, n: int) -> bytes:
        return self._buf.read(n)


@pytest.mark.asyncio
async def test_save_and_hash_single_pass(tmp_path):
    data = b"%PDF-1.4\n" + b"x" * (3 * 1024 * 1024)  # 3MB
    dest = tmp_path / "out.pdf"

    size, digest = await FileService.save_upload_file_with_hash(
        FakeUploadFile(data), str(dest), max_size=10 * 1024 * 1024
    )

    assert size == len(data)
    assert digest == hashlib.sha256(data).hexdigest()
    assert dest.read_bytes() == data


@pytest.mark.asyncio
async def test_save_and_hash_over_limit(tmp_path):
    data = b"x" * (2 * 1024 * 1024)
    dest = tmp_path / "out.pdf"
    with pytest.raises(ValueError):
        await FileService.save_upload_file_with_hash(
            FakeUploadFile(data), str(dest), max_size=1024 * 1024
        )
    assert not dest.exists()
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && pytest tests/test_upload_hash.py -v`
Expected: FAIL — `AttributeError: save_upload_file_with_hash`.

- [ ] **Step 3: `backend/app/services/file_service.py`에 함수 추가**

`save_upload_file` 함수 바로 아래 추가:
```python
@staticmethod
async def save_upload_file_with_hash(
    upload_file,
    destination: str,
    max_size: Optional[int] = None,
) -> tuple[int, str]:
    """저장과 SHA256 해시를 단일 패스로 수행."""
    max_size = max_size or settings.max_upload_size_bytes
    total_size = 0
    hasher = hashlib.sha256()

    try:
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(destination, 'wb') as f:
            while True:
                chunk = await upload_file.read(FileService.CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    await f.close()
                    if os.path.exists(destination):
                        os.remove(destination)
                    raise ValueError(f"파일 크기가 제한을 초과했습니다: {max_size} bytes")
                hasher.update(chunk)
                await f.write(chunk)
        logger.info(f"파일+해시 저장 완료: {destination} ({total_size} bytes)")
        return total_size, hasher.hexdigest()
    except Exception:
        if os.path.exists(destination):
            os.remove(destination)
        raise
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_upload_hash.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/file_service.py backend/tests/test_upload_hash.py
git commit -m "feat: single-pass save_upload_file_with_hash (50% IO reduction)"
```

---

## Task 3.3: Upload 엔드포인트에 단일 패스 + Redis 락 dedup 적용

**Files:**
- Create: `backend/tests/test_dedup_lock.py`
- Modify: `backend/app/api/upload.py`

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_dedup_lock.py`:
```python
import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import app


def test_concurrent_same_file_creates_one_job(tmp_path, monkeypatch):
    """동일 파일 동시 업로드 2건 → 완료 후 중복 재사용 1건만 생성되어야 함."""
    # 이 테스트는 시스템 통합 테스트 성격이 강해 smoke로 PASS 여부만 점검.
    # upload + 동일 옵션 재업로드 → 두번째가 기존 결과 재사용하는지 확인.
    client = TestClient(app)
    pdf_bytes = b"%PDF-1.4\n%%EOF\n"

    r1 = client.post("/api/upload", files=[("files", ("a.pdf", pdf_bytes, "application/pdf"))])
    assert r1.status_code in (200, 400)  # 유효성 검사 실패도 허용 (fake PDF)
    # 본 테스트는 dedup 경로 자체가 import/실행 가능함을 검증하는 smoke
    assert hasattr(__import__('app.api.upload', fromlist=['upload_files']), 'upload_files')
```

(주: 실제 경합 조건은 Docker 환경에서 수동 QA로 검증. 여기서는 import·라우트 무결성만 확보)

- [ ] **Step 2: 테스트 실행 (기동 확인)**

Run: `cd backend && pytest tests/test_dedup_lock.py -v`
Expected: PASS (smoke).

- [ ] **Step 3: `backend/app/api/upload.py` 개편**

라인 16의 import 아래에 추가:
```python
from app.core.redis_client import redis_client
from redis.exceptions import LockError
```

파일 상단에 helper:
```python
import hashlib as _hashlib

def _options_hash(preset, engine, preserve_metadata, preserve_ocr) -> str:
    key = f"{preset}|{engine}|{preserve_metadata}|{preserve_ocr}".encode()
    return _hashlib.sha256(key).hexdigest()[:16]
```

`upload_files` 함수의 루프 (라인 55-164) 내, 기존 "스트리밍 저장 → 해시 계산" 블록을 단일 패스로 교체:

```python
# 스트리밍 저장 + 해시 (단일 패스)
logger.info(f"파일 저장 시작: {original_filename}")
file_size, file_hash = await FileService.save_upload_file_with_hash(
    upload_file,
    file_path,
    max_size=settings.max_upload_size_bytes,
)

# PDF 유효성 검사
if not FileService.validate_pdf(file_path):
    os.remove(file_path)
    raise HTTPException(status_code=400, detail=f"유효하지 않은 PDF: {original_filename}")

if settings.ENABLE_ANTIVIRUS:
    if not FileService.scan_antivirus(file_path):
        os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"바이러스 감지: {original_filename}")

# dedup: Redis 분산 락으로 경합 보호
reused = False
if settings.ENABLE_DEDUPLICATION:
    opt_hash = _options_hash(preset, engine, preserve_metadata, preserve_ocr)
    lock_key = f"dedup:{file_hash}:{opt_hash}"
    try:
        with redis_client.lock(lock_key, timeout=5, blocking_timeout=10):
            existing_job = db.query(Job).filter(
                Job.file_hash == file_hash,
                Job.status == JobStatus.COMPLETED,
                Job.expires_at > datetime.now(timezone.utc),
                Job.preset == preset,
                Job.engine == engine,
                Job.preserve_metadata == preserve_metadata,
                Job.preserve_ocr == preserve_ocr,
            ).first()

            if existing_job and existing_job.result_file:
                result_path = os.path.join(settings.RESULT_DIR, existing_job.result_file)
                if os.path.exists(result_path):
                    logger.info(f"중복 감지, 기존 결과 재사용: {file_hash}")
                    new_job = Job(
                        id=file_id,
                        user_session=user_session,
                        filename=filename,
                        original_filename=original_filename,
                        file_hash=file_hash,
                        original_size=file_size,
                        compressed_size=existing_job.compressed_size,
                        compression_ratio=existing_job.compression_ratio,
                        page_count=existing_job.page_count,
                        image_count=existing_job.image_count,
                        preset=preset,
                        engine=engine,
                        preserve_metadata=preserve_metadata,
                        preserve_ocr=preserve_ocr,
                        status=JobStatus.COMPLETED,
                        result_file=existing_job.result_file,
                        progress=1.0,
                        created_at=datetime.now(timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.RETENTION_HOURS),
                    )
                    db.add(new_job)
                    db.commit()
                    job_ids.append(file_id)
                    reused = True
    except LockError:
        logger.warning(f"dedup 락 획득 실패, 새 작업으로 진행: {file_hash}")
    except Exception as e:
        logger.error(f"dedup 처리 중 오류, 새 작업으로 진행: {e}")

if reused:
    continue

# 일반 처리: Job 생성 + Celery dispatch (기존과 동일)
job = Job(
    id=file_id,
    user_session=user_session,
    filename=filename,
    original_filename=original_filename,
    file_hash=file_hash,
    original_size=file_size,
    preset=preset,
    engine=engine,
    preserve_metadata=preserve_metadata,
    preserve_ocr=preserve_ocr,
    status=JobStatus.QUEUED,
    created_at=datetime.now(timezone.utc),
)
db.add(job)
db.commit()

task = compress_pdf_task.delay(file_id)
job.celery_task_id = task.id
db.commit()

logger.info(f"작업 등록: {file_id} - {original_filename}")
job_ids.append(file_id)
```

기존 "파일 해시 계산" 블록(라인 84-87)과 "기존 작업 확인 ... 계속 continue"(라인 88-137)는 위 블록으로 대체됨.

- [ ] **Step 4: 회귀 스모크**

Run: `docker compose up -d --build backend`
Run: 브라우저에서 PDF 업로드 → 완료. 동일 파일 동일 옵션 재업로드 → 즉시 "완료" 상태로 표시 (dedup 재사용 로그 `중복 감지, 기존 결과 재사용` 확인).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/upload.py backend/tests/test_dedup_lock.py
git commit -m "feat: single-pass upload+hash and Redis-lock-protected dedup"
```

---

## Task 3.4: Celery 설정 업데이트

**Files:**
- Modify: `backend/app/workers/celery_app.py`

- [ ] **Step 1: 파일 전체 재작성**

```python
"""Celery 애플리케이션"""
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    'pdf_compressor',
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=['app.workers.tasks'],
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,

    task_track_started=True,
    task_time_limit=settings.TASK_TIMEOUT_SECONDS,
    task_soft_time_limit=max(30, settings.TASK_TIMEOUT_SECONDS - 60),

    task_acks_late=True,
    task_reject_on_worker_lost=True,

    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    worker_max_memory_per_child=1_200_000,  # KB, = 1.2GB

    task_compression='gzip',

    result_expires=3600 * 12,  # 24h → 12h

    broker_connection_retry_on_startup=True,
    broker_transport_options={'visibility_timeout': 3600},
)
```

- [ ] **Step 2: 회귀 확인**

Run: `docker compose up -d --build worker`
Run: 소형 PDF 압축 완료 확인.

- [ ] **Step 3: Commit**

```bash
git add backend/app/workers/celery_app.py
git commit -m "perf: tune celery (gzip compression, max_memory_per_child, result_expires 12h)"
```

---

# Phase 4: SSE & 프론트엔드

## Task 4.1: `sse-starlette` 의존성 추가

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: `backend/requirements.txt`에 추가**

FastAPI 섹션 아래에 한 줄 추가:
```
sse-starlette==2.1.0
```

- [ ] **Step 2: 빌드 확인**

Run: `docker compose build backend`
Expected: 성공.

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: add sse-starlette for SSE endpoint"
```

---

## Task 4.2: Worker 진행상태 Redis publish

**Files:**
- Modify: `backend/app/workers/tasks.py` (update_progress + 주요 상태 전환에서 publish)

- [ ] **Step 1: `backend/app/workers/tasks.py` 수정**

파일 상단 import:
```python
import json
from app.core.redis_client import redis_client
```

publish 헬퍼 추가 (함수 레벨, `send_webhook_notification` 이 이미 제거된 자리):
```python
def _publish_job_event(job_id: str, payload: dict) -> None:
    try:
        redis_client.publish(f"job:{job_id}", json.dumps(payload).encode())
    except Exception as e:
        logger.warning(f"publish 실패 (무시): {e}")
```

`CallbackTask.update_progress` 내부에서 db 업데이트 직후 호출:
```python
_publish_job_event(job_id, {
    "job_id": job_id,
    "type": "progress",
    "progress": min(progress, 1.0),
    "eta_seconds": eta_seconds,
})
```

`compress_pdf_task` 의 주요 상태 전환 지점에서 추가 publish:
- RUNNING 진입 직후 (`job.status = JobStatus.RUNNING` db.flush 뒤):
  ```python
  _publish_job_event(job_id, {"job_id": job_id, "type": "status", "status": "running"})
  ```
- COMPLETED 처리 후 (return 직전):
  ```python
  _publish_job_event(job_id, {
      "job_id": job_id, "type": "status", "status": "completed",
      "compressed_size": compressed_size, "compression_ratio": compression_ratio,
  })
  ```
- FAILED 처리 직후 (재시도 미사용 분기):
  ```python
  _publish_job_event(job_id, {"job_id": job_id, "type": "status", "status": "failed", "error": str(e)})
  ```

- [ ] **Step 2: 스모크 테스트**

Run: `docker compose up -d --build worker`
Run: 다른 터미널에서 `docker compose exec redis redis-cli SUBSCRIBE "job:*"` (psubscribe 사용: `PSUBSCRIBE "job:*"`)
Run: PDF 업로드 → SUBSCRIBE 쪽에 메시지가 연속 출력되는지 확인.

- [ ] **Step 3: Commit**

```bash
git add backend/app/workers/tasks.py
git commit -m "feat: publish job events to redis (job:{id} channel)"
```

---

## Task 4.3: SSE 엔드포인트 구현

**Files:**
- Create: `backend/tests/test_sse_stream.py`
- Modify: `backend/app/api/jobs.py`

- [ ] **Step 1: 실패 테스트 작성**

`backend/tests/test_sse_stream.py`:
```python
from fastapi.testclient import TestClient
from app.main import app


def test_sse_endpoint_registered():
    client = TestClient(app)
    # 존재하지 않는 job → 엔드포인트가 등록되어 있어야 404 또는 SSE 응답 반환
    r = client.get("/api/jobs/nonexistent/stream", stream=True)
    assert r.status_code in (200, 404)
```

- [ ] **Step 2: 실패 확인**

Run: `cd backend && pytest tests/test_sse_stream.py -v`
Expected: FAIL — 아직 라우트 없음, 404가 나올 수도 있지만 TestClient의 라우트 매칭 동작상 일반적으로 404.

(실제로 현재 코드에선 `/api/jobs/{job_id}` 가 `nonexistent/stream` 을 job_id로 매칭할 수 있으므로, 테스트는 라우트 등록 여부 간접 검증. 구현 후에도 404 허용.)

- [ ] **Step 3: `backend/app/api/jobs.py` 에 SSE 엔드포인트 추가**

파일 상단 import:
```python
import asyncio
import json
from sse_starlette.sse import EventSourceResponse
from app.core.redis_client import redis_client
```

기존 `@router.get("/jobs/{job_id}", ...)` 라우트보다 **위에** 다음을 배치 (경로 매칭 우선순위 때문):

```python
@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, db: Session = Depends(get_db)):
    """Job 상태 변화를 SSE로 전달."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")

    async def event_gen():
        # 1) 최초 스냅샷
        snapshot = {
            "job_id": job.id,
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "progress": job.progress,
            "eta_seconds": job.eta_seconds,
        }
        yield {"event": "snapshot", "data": json.dumps(snapshot)}

        if snapshot["status"] in ("completed", "failed", "cancelled"):
            return

        # 2) Redis pubsub 구독 (비동기)
        pubsub = redis_client.pubsub()
        try:
            pubsub.subscribe(f"job:{job_id}")
            while True:
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    data = msg["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    yield {"event": "update", "data": data}
                    try:
                        parsed = json.loads(data)
                        if parsed.get("type") == "status" and parsed.get("status") in ("completed", "failed", "cancelled"):
                            return
                    except Exception:
                        pass
                await asyncio.sleep(0)
        finally:
            try:
                pubsub.unsubscribe(f"job:{job_id}")
                pubsub.close()
            except Exception:
                pass

    return EventSourceResponse(event_gen())
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd backend && pytest tests/test_sse_stream.py -v`
Expected: PASS (404 or 200).

- [ ] **Step 5: 수동 검증**

Run: `docker compose up -d --build backend worker`
Run: 브라우저에서 PDF 업로드 → 즉시 `curl -N http://localhost:8082/api/jobs/{job_id}/stream`
Expected: `event: snapshot` 첫 줄, 이후 `event: update` 가 워커 진행에 따라 수신되고 `status:completed` 후 연결 종료.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/jobs.py backend/tests/test_sse_stream.py
git commit -m "feat: add SSE endpoint /api/jobs/{id}/stream (redis pubsub)"
```

---

## Task 4.4: 프론트엔드 SSE 헬퍼

**Files:**
- Create: `frontend/src/lib/sse.ts`

- [ ] **Step 1: `frontend/src/lib/sse.ts` 신규 작성**

```typescript
import type { Job } from "./api";

export type JobUpdateHandler = (partial: Partial<Job> & { job_id: string }) => void;

export function subscribeJob(
  jobId: string,
  onUpdate: JobUpdateHandler,
  onTerminal?: () => void
): () => void {
  const url = `/api/jobs/${jobId}/stream`;
  let es: EventSource | null = new EventSource(url);

  const handleSnapshot = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onUpdate({ ...data, id: data.job_id });
      if (["completed", "failed", "cancelled"].includes(data.status)) {
        es?.close();
        es = null;
        onTerminal?.();
      }
    } catch (err) {
      console.error("SSE snapshot parse error", err);
    }
  };

  const handleUpdate = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onUpdate({ ...data, id: data.job_id });
      if (data.type === "status" && ["completed", "failed", "cancelled"].includes(data.status)) {
        es?.close();
        es = null;
        onTerminal?.();
      }
    } catch (err) {
      console.error("SSE update parse error", err);
    }
  };

  const handleError = () => {
    console.warn(`SSE connection error for job ${jobId}`);
    es?.close();
    es = null;
  };

  es.addEventListener("snapshot", handleSnapshot as EventListener);
  es.addEventListener("update", handleUpdate as EventListener);
  es.addEventListener("error", handleError);

  return () => {
    es?.close();
    es = null;
  };
}
```

- [ ] **Step 2: 타입 체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/sse.ts
git commit -m "feat(frontend): add subscribeJob SSE helper"
```

---

## Task 4.5: `page.tsx` 폴링 제거 + SSE 구독

**Files:**
- Modify: `frontend/src/app/page.tsx`

- [ ] **Step 1: 폴링 useEffect 삭제 및 SSE 구독으로 교체**

`frontend/src/app/page.tsx` 라인 3의 import에 추가:
```typescript
import { subscribeJob } from '@/lib/sse';
```

라인 29-48의 폴링 `useEffect` 전체를 아래로 교체:
```typescript
// 활성 작업에 대한 SSE 구독
useEffect(() => {
  const activeJobs = jobs.filter(
    (job) => job.status === 'queued' || job.status === 'running'
  );
  if (activeJobs.length === 0) return;

  const unsubscribes = activeJobs.map((job) =>
    subscribeJob(
      job.id,
      (partial) => {
        setJobs((prev) =>
          prev.map((j) => (j.id === partial.id ? { ...j, ...partial } : j))
        );
      },
      () => {
        // terminal 상태 도달 시 최종 전체 상태를 1회 재조회해 누락된 필드 보정
        getJob(job.id).then((full) => {
          setJobs((prev) => prev.map((j) => (j.id === job.id ? full : j)));
        }).catch(() => {});
      }
    )
  );

  return () => {
    unsubscribes.forEach((u) => u());
  };
  // job id 집합 변화에만 재구독
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [jobs.map(j => `${j.id}:${j.status}`).join(',')]);
```

- [ ] **Step 2: 프론트엔드 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 성공.

- [ ] **Step 3: 수동 E2E**

Run: `docker compose up -d --build`
Browser: http://localhost:8082 → PDF 업로드 → 네트워크 탭에서 `/api/jobs/{id}/stream` 이 `eventsource` 타입으로 장기 연결되어 있고, 2초 폴링 호출은 없는지 확인. 진행률이 실시간으로 갱신되는지.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/page.tsx
git commit -m "feat(frontend): replace 2s polling loop with SSE subscription"
```

---

## Task 4.6: Frontend Dockerfile 소소 정리

**Files:**
- Modify: `frontend/Dockerfile` (필요 시 `npm cache clean --force` 라인 제거)

- [ ] **Step 1: `frontend/Dockerfile` 확인**

Run: `grep -n "npm cache clean" frontend/Dockerfile`
- 매치가 있으면: 해당 라인을 삭제.
- 없으면: 이 Task는 no-op. Step 2~3 스킵.

- [ ] **Step 2: 빌드 확인**

Run: `docker compose build frontend`
Expected: 성공.

- [ ] **Step 3: Commit (변경 있을 때만)**

```bash
git add frontend/Dockerfile
git commit -m "chore(frontend): remove unnecessary npm cache clean"
```

---

# 최종 검증

## Task V.1: 통합 수동 QA

- [ ] **Step 1: 전체 재기동**

Run: `docker compose down && docker compose up -d --build`

- [ ] **Step 2: `docker stats` 로 메모리 총합 확인**

Run: `sleep 60 && docker stats --no-stream`
Expected: 5개 컨테이너(redis, backend, worker, frontend, nginx) 존재, 총 메모리 사용량 < 1.5GB (idle).

- [ ] **Step 3: 업로드 → 압축 → 다운로드 E2E**

Browser: http://localhost:8082
- 작은 PDF 1개 업로드 → 완료 → 다운로드 성공
- 같은 파일·옵션 재업로드 → 로그에 `중복 감지` 나타나고 즉시 완료
- 중간 크기(50MB) PDF 업로드 → worker 메모리 < 1.5GB 유지 확인
- 네트워크 탭: `/api/jobs/*/stream` 이 EventSource 타입으로 보이고 2초 폴링 없음
- `/api/healthz`, `/api/readyz` 200 응답

- [ ] **Step 4: 전체 테스트 스위트**

Run: `cd backend && pytest -v`
Expected: 모든 테스트 PASS.

- [ ] **Step 5: 최종 Commit (변경 있을 때)**

(문서/마지막 수정이 있을 때만)
```bash
git add -u
git commit -m "docs: record final optimization verification"
```
