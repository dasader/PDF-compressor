# PDF Compressor 경량화·효율화 설계 문서

- **작성일:** 2026-04-14
- **범위:** 인프라, 백엔드, 프론트엔드 전체 (옵션 C)
- **목표:** 메모리 40% 절감, 동시성 안정화, I/O 50% 감소, 실시간 통신(SSE) 도입

---

## 0. 배경 및 현황

현재 스택은 FastAPI + Celery + Redis + SQLite + Next.js로 구성되며 Docker Compose로 6개 서비스를 운영한다. 기능은 정상 동작하나 다음 관점에서 비효율·잠재 버그가 발견되었다.

- **메모리 limit 총합 3.89GB**, 실사용 대비 과다 할당
- **Beat 컨테이너**가 cleanup 1개만 스케줄하면서 128MB 별도 점유
- **SQLite WAL 미적용** + 복합 인덱스 부재
- **Nginx gzip/keepalive/proxy_buffer** 전부 미설정
- **업로드 시 파일을 2회 읽음** (저장 + 해시 계산 분리)
- **Dedup 경합**: 동시 업로드 시 race condition 가능
- **프론트 2초 폴링**으로 서버 부하 (SSE 대체 가능)
- **불필요 의존성**: 기본 비활성인 prometheus-client, httpx, tenacity

## 1. 목표 (성공 기준)

| 항목 | 현재 | 목표 |
|------|------|------|
| 메모리 limit 총합 | 3.89GB | ≤ 3.0GB |
| 컨테이너 수 | 6개 | 5개 |
| 업로드 경로 파일 I/O | 2회 읽기 | 1회 (단일 패스) |
| Job 상태 업데이트 지연 | ~2초 (폴링) | ~10ms (SSE) |
| 대용량 PDF(≤512MB) 안정 처리 | 미보장 | Worker 1.5GB 내 안정 |
| 백엔드 이미지 크기 | 현재 대비 | -30~40% |

## 2. 비목표 (Out of Scope)

- PostgreSQL 전환 (SQLite 유지)
- Kubernetes 전환
- Prometheus/웹훅 기능 복구 — **완전 제거**
- 인증/권한 추가
- 업로드 청크 API(`/api/upload-chunk`) 프론트 연동

---

## 3. 아키텍처 개요

### 3.1 서비스 구성 변화

```
변경 전: redis, backend, worker, beat, frontend, nginx (6개)
변경 후: redis, backend, worker+beat, frontend, nginx   (5개)
```

### 3.2 실시간 통신 흐름 (SSE + Redis Pub/Sub)

```
[Worker]
  progress/status 업데이트
      ├─> DB UPDATE (기존)
      └─> redis.publish("job:{id}", payload) (신규)
                                 │
                                 ▼
[Backend SSE endpoint]
  GET /api/jobs/{id}/stream
    1) 최초 연결 시 DB에서 현재 상태 1회 전송(스냅샷)
    2) redis.pubsub.subscribe("job:{id}")
    3) 메시지 수신 → SSE event로 전달
    4) status ∈ {completed, failed, cancelled} → 연결 종료
                                 │
                                 ▼
[Browser]
  EventSource("/api/jobs/{id}/stream")
    - 기존 2초 setInterval 폴링 제거
    - 연결 실패 시 지수 백오프 폴링 폴백(5s → 30s)
```

---

## 4. 상세 설계

### 4.1 인프라 & 컨테이너

#### 4.1.1 Beat를 Worker에 병합
- `docker-compose.yml`의 `beat` 서비스 블록 삭제
- `backend/worker-entrypoint.sh`의 celery 실행 명령에 `-B` 플래그 추가
- 기존 beat 엔트리 스크립트 파일 삭제

#### 4.1.2 Redis 튜닝 (`docker-compose.yml`)
```yaml
command: >
  redis-server
  --appendonly no
  --save ""
  --maxmemory 384mb
  --maxmemory-policy volatile-lru
```
- AOF 비활성, RDB도 비활성 (큐·결과는 휘발 허용)
- TTL 있는 키만 축출하도록 정책 변경

#### 4.1.3 Nginx 최적화 (`nginx.conf`)
- `gzip on` + 텍스트 계열 MIME
- `upstream` 블록에 `keepalive 32`
- `proxy_buffer_size 4k; proxy_buffers 8 16k; proxy_busy_buffers_size 32k;`
- 정적 자산(`/_next/static/`) 1년 immutable 캐시
- **대용량 업로드 지원:**
  - `client_max_body_size 600M;`
  - `client_body_timeout 600s;`
  - 업로드 경로(`/api/upload`)는 `proxy_request_buffering off;`
- **SSE 전용 location 블록:**
  - `proxy_buffering off; proxy_cache off;`
  - `proxy_read_timeout 3600s;`
  - `proxy_http_version 1.1; proxy_set_header Connection "";`

#### 4.1.4 메모리 limit 재조정

| 서비스 | 기존 | 변경 | 근거 |
|--------|------|------|------|
| redis | 384M | 512M | AOF 제거 여유분을 캐시·pubsub 버퍼로 |
| backend | 800M | 640M | FastAPI 실측 <300M + 다운로드 버퍼 여유 |
| worker | 2G | 1.5G | 대용량 PDF(pikepdf 전체 로딩) 피크 대비 |
| beat | 128M | (삭제) | worker 병합 |
| frontend | 512M | 256M | Next.js standalone 실사용 |
| nginx | 64M | 64M | 유지 |
| **총** | **3.89G** | **~3.0G** | **-23%** |

#### 4.1.5 Worker 안전장치
- Celery 설정: `worker_max_memory_per_child=1200000` (1.2GB 초과 시 자동 재시작)

### 4.2 백엔드

#### 4.2.1 SQLite 고도화 (`backend/app/models/database.py`)
- `sqlalchemy.event.listens_for(engine, "connect")` 로 pragma 적용:
  - `journal_mode=WAL`
  - `synchronous=NORMAL`
  - `cache_size=-32000` (32MB)
  - `mmap_size=134217728` (128MB)
- `create_engine` 파라미터: `pool_size=5, max_overflow=10, connect_args={"timeout": 30}`

#### 4.2.2 복합 인덱스 (`backend/app/models/job.py`)
```python
__table_args__ = (
    Index("idx_user_status", "user_session", "status"),
    Index("idx_expires_created", "expires_at", "created_at"),
)
```
- 마이그레이션 방식: 기동 시 `CREATE INDEX IF NOT EXISTS` 실행 헬퍼 추가

#### 4.2.3 업로드 단일 패스 (`backend/app/services/file_service.py`, `api/upload.py`)
- 신규 함수: `save_upload_file_with_hash(upload_file, dest_path) -> (size, sha256)`
  - 청크 읽기 루프 안에서 `hashlib.sha256.update()` 와 `aiofiles.write()` 동시 수행
- `api/upload.py`에서 기존 "저장 → 재오픈 → 해시" 3단계 → 1단계 호출
- 기존 `calculate_file_hash` 함수는 재사용 가능 경로가 없으면 제거

#### 4.2.4 Dedup Redis 분산 락 (`backend/app/api/upload.py`)
- 락 키: `dedup:{file_hash}:{options_hash}`
- `redis_client.lock(key, timeout=5, blocking_timeout=10)` 컨텍스트 내에서 dedup 조회 및 Job 생성
- 락 획득 실패(타임아웃) → 새 Job 생성으로 fallback (fail-safe)

#### 4.2.5 Celery 설정 (`backend/app/workers/celery_app.py`)
```python
result_expires = 3600 * 12          # 24h → 12h
task_compression = "gzip"
worker_max_memory_per_child = 1_200_000
worker_max_tasks_per_child = 50
task_acks_late = True
task_reject_on_worker_lost = True
broker_transport_options = {"visibility_timeout": 3600}
```

#### 4.2.6 DB 세션 컨텍스트 매니저 (`backend/app/models/database.py`, `workers/tasks.py`)
- `@contextmanager def db_session()` 헬퍼 추가 (try/commit/rollback/close)
- `workers/tasks.py`의 모든 작업에서 `with db_session() as db:` 사용
- 예외 발생 시 세션 누수 방지

#### 4.2.7 Cleanup 개선 (`backend/app/workers/tasks.py::cleanup_old_files_task`)
- `db.query(Job).filter(...).limit(200)` 배치 루프
- 만료된 Job의 **업로드 원본 + 결과 파일 모두** 삭제
- `services/file_service.cleanup_old_files` (mtime 기반) 제거 — DB 기준으로 통일

#### 4.2.8 타임아웃 현실화 (`backend/app/core/config.py`)
- `TASK_TIMEOUT_SECONDS`: 1800 → **900**
- `MAX_UPLOAD_SIZE_MB`: 512 유지 (Nginx `client_max_body_size`와 일치)

#### 4.2.9 의존성 완전 제거
**제거할 패키지 (`backend/requirements.txt`):**
- `prometheus-client`
- `httpx`
- `tenacity`

**제거할 코드:**
- `backend/app/main.py`: Prometheus 마운트 블록
- `backend/app/workers/tasks.py`: 웹훅 호출 로직 (~188번째 줄 부근)
- `backend/app/core/config.py`: `ENABLE_METRICS`, `WEBHOOK_ENABLED`, `WEBHOOK_URL` 등 관련 설정값
- `env.example`: 위 ENV 삭제

**교체:**
- tenacity 사용처 → Celery `autoretry_for`, `retry_backoff` 로 대체

#### 4.2.10 Dockerfile 멀티스테이지 (`backend/Dockerfile`)
- Stage 1 (`builder`): build-essential, libmagic-dev 설치 후 `pip install --user`
- Stage 2 (`runtime`): ghostscript, qpdf, libmagic1만 설치 + `COPY --from=builder /root/.local /root/.local`
- 빌드 도구가 최종 이미지에 포함되지 않도록 분리

### 4.3 프론트엔드 & 실시간 통신

#### 4.3.1 SSE 엔드포인트 신설 (`backend/app/api/jobs.py`)
- 라우트: `GET /api/jobs/{job_id}/stream`
- 라이브러리: `sse-starlette` (requirements.txt 추가)
- 동작:
  1. 연결 직후 DB에서 현재 Job 1회 조회 → `event: snapshot` 전송
  2. Redis pubsub 채널 `job:{job_id}` 구독
  3. 수신 메시지 → `event: update` 로 중계
  4. terminal status 도달 시 연결 종료
  5. Job이 없으면 `event: error` 후 종료

#### 4.3.2 Worker publish 지점 (`backend/app/workers/tasks.py`)
- progress 업데이트 헬퍼 확장: DB 업데이트 직후 `redis_client.publish(f"job:{job_id}", json.dumps(payload))`
- payload 스키마:
  ```json
  {"job_id": "...", "status": "running", "progress": 0.42, "message": "...", "updated_at": "..."}
  ```

#### 4.3.3 프론트엔드 SSE 구독 (`frontend/src/lib/api.ts`, `app/page.tsx`)
- `subscribeJob(jobId, onUpdate)` 헬퍼: `EventSource` 생성 + 리스너 등록 + 해제 함수 반환
- `page.tsx`:
  - 기존 `setInterval(..., 2000)` 제거
  - 각 active job에 대해 `useEffect` 안에서 subscribe, cleanup에서 close
  - SSE 연결 실패 감지 시 **폴링 폴백**: 5초 → 30초 지수 백오프
- 완료/실패/취소 job은 구독 대상 제외 (기존 로직 유지)

#### 4.3.4 유지 사항
- `react-dropzone`: 유지
- `lucide-react`: 유지 (tree-shaking 신뢰)
- 프론트 Dockerfile: 현재 멀티스테이지 + standalone 구조 유지, `npm cache clean --force` 라인만 제거

---

## 5. 데이터 흐름 변화 요약

### 업로드
```
[Before] Client → Nginx(buffer) → Backend(save) → Backend(re-read for hash) → DB → Celery
[After]  Client → Nginx(stream)  → Backend(save+hash 단일 패스) → DB(with Redis lock dedup) → Celery
```

### 진행 상태
```
[Before] Client(2s polling) → Backend → DB
[After]  Worker → DB + Redis.publish → Backend(pubsub) → Client(SSE)
```

---

## 6. 에러 처리 / 폴백 전략

| 상황 | 동작 |
|------|------|
| Dedup 락 획득 실패 (Redis 장애 등) | 락 없이 새 Job 생성 (성능 대 기능 트레이드 — 기능 우선) |
| SSE 연결 실패 | 클라이언트가 5s→30s 지수 백오프 폴링으로 폴백 |
| Redis pubsub 메시지 드롭 | 1) 최초 스냅샷으로 초기 상태 확보, 2) 재연결 시 snapshot 재전송 |
| Worker OOM (>1.2GB) | `max_memory_per_child` 로 자동 재시작, Celery가 task 재큐 |
| Ghostscript/qpdf 타임아웃 (>15분) | Celery 타임아웃 → Job을 `failed` 처리 |
| SQLite busy | `PRAGMA busy_timeout` + SQLAlchemy `timeout=30` |

---

## 7. 테스트 계획

### 단위/통합 테스트 (`backend/tests/`)
- `test_upload_hash.py`: 단일 패스 save+hash가 기존 2단계와 동일 해시 산출
- `test_dedup_lock.py`: 동시 업로드 2개 → 단 1개의 Job만 새로 생성됨 (asyncio.gather 시뮬레이션)
- `test_sse_stream.py`: Redis publish → SSE client 수신 검증
- `test_sse_snapshot.py`: 연결 직후 현재 상태 1회 수신
- `test_cleanup_batching.py`: 500개 만료 레코드 → 배치 루프로 전량 정리
- `test_db_pragma.py`: 엔진 생성 후 `PRAGMA journal_mode` = `wal`

### 부하/회귀 테스트
- 512MB PDF 업로드 → Worker 메모리 < 1.5GB, 15분 이내 완료
- 100개 동시 SSE 연결 → backend CPU / 메모리 안정
- Prometheus/webhook 제거 후 정상 부팅 확인

### 수동 QA
- 브라우저에서 업로드 → 진행률이 폴링 없이 즉시 갱신되는지
- 네트워크 탭에서 `/api/jobs/.../stream` 장기 연결 1개만 있는지
- `docker stats` 로 메모리 사용량 < 3GB 총합 확인

---

## 8. 마이그레이션 / 배포 순서

1. **Phase 0 (파괴적 변경 없음, 선행)**
   - 의존성 완전 제거 + Prometheus/webhook 코드 삭제
   - Dockerfile 멀티스테이지 재작성
   - 메모리 limit 재조정
2. **Phase 1 (인프라)**
   - Beat → Worker 병합
   - Redis 설정 변경
   - Nginx gzip/keepalive/proxy_buffer/SSE location
3. **Phase 2 (백엔드 데이터 계층)**
   - SQLite pragma + 복합 인덱스
   - DB 세션 context manager 도입
   - 타임아웃 값 변경
   - Cleanup 배치화 + 원본 동반 삭제
4. **Phase 3 (업로드 & dedup)**
   - 단일 패스 save+hash
   - Redis 분산 락 dedup
5. **Phase 4 (실시간 통신)**
   - Worker publish 추가
   - SSE endpoint 신설
   - 프론트엔드 SSE 전환 + 폴링 폴백

각 Phase 완료 후 `docker compose up -d --build` 으로 회귀 확인.

---

## 9. 위험 요소

| 위험 | 영향 | 완화책 |
|------|------|--------|
| Worker 메모리 1.5GB로 축소 후 대용량 PDF OOM | 작업 실패 | `max_memory_per_child` 자동 재시작 + Celery 재큐 |
| SSE가 Nginx/프록시 환경에서 끊김 | 실시간성 상실 | 폴링 폴백 유지 |
| Redis pub/sub 메시지 손실 | 중간 진행률 누락 | 최초 snapshot + terminal status는 DB 기준 재확인 |
| Redis 락 장애 → dedup 우회 | 중복 압축 (기능 정상) | 허용, 로그 경고 |
| SQLite WAL 마이그레이션 시 락 충돌 | 첫 기동 실패 | 기동 스크립트에서 안전 재시도 |
| Prometheus/webhook 의존 운영 중? | 기존 배포 파괴 | **현재 기본 비활성** 확인됨, 영향 없음 |

---

## 10. 참고

- 본 문서는 설계(Spec)이며 구체 구현 단계는 별도 implementation plan으로 작성됨 (`docs/superpowers/plans/`).
- 각 Phase는 독립 커밋·PR 단위로 진행 가능하도록 경계가 분리되어 있다.
