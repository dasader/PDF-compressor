# PDF Compressor

대용량 PDF 파일을 빠르고 간편하게 압축하는 웹 애플리케이션입니다.
Next.js 프론트엔드, FastAPI 백엔드, Celery 비동기 워커로 구성된 풀스택 Docker 환경에서 동작합니다.

---

## 주요 기능

- **드래그 앤 드롭** 파일 업로드 (최대 20개 동시, 파일당 512MB)
- **4가지 압축 프리셋** (screen / ebook / printer / prepress)
- **2가지 압축 엔진** — 최대 압축(Ghostscript, 손실) / 무손실(pikepdf), 자동 폴백
- **실시간 진행률** 표시 (SSE 스트림, 작업별 구독)
- **중복 파일 감지** (SHA-256 해시 기반 결과 재사용)
- **배치 ZIP 다운로드** (여러 파일 동시 다운로드)
- **서버 간 연동 API** (`POST /api/compress` — 보내면 압축본을 바로 반환)
- **24시간 자동 파일 만료** 및 정리 스케줄러
- **암호화된 PDF 자동 거부**
- 한글 파일명 다운로드 지원 (RFC 5987)

---

## 아키텍처

```
Browser
  │
  ▼
Nginx (port 8106)  ──────────────────────────────┐
  │                                              │
  ▼ /api/*                                       ▼ /*
FastAPI Backend (내부)                  Next.js Frontend (내부)
  │
  ├── SQLite (job 메타데이터, WAL)
  ├── Redis (Celery 브로커 + SSE pub/sub)
  │
  ▼
Celery Worker (압축 실행)
  │
  ├── Ghostscript (손실 — 이미지 다운샘플)
  └── pikepdf (무손실 — 구조 최적화)

Celery Beat (worker에 내장, 매시간 만료 파일 정리)
```

### 요청 흐름

1. 브라우저 → `POST /api/upload` → 파일 저장 + Job DB 레코드 생성 + Celery 태스크 큐 등록
2. Celery Worker → PDF 압축 실행 → `/data/results/` 저장
3. 프론트엔드 → `GET /api/jobs/{id}/stream` (SSE) 구독 → 접속 즉시 스냅샷, 이후 Redis pub/sub 채널 `job:{id}`의 진행률·상태 이벤트 수신
4. `GET /api/jobs/{id}/download` → 압축 파일 전송

---

## 압축 프리셋

| 프리셋 | DPI | JPEG 품질 | 용도 |
|--------|-----|-----------|------|
| `screen` | 72 | 30% | 최대 압축 (화면 열람용) |
| `ebook` | 150 | 60% | **기본값** — 균형 |
| `printer` | 300 | 80% | 인쇄 품질 |
| `prepress` | 300 | 90% | 고품질 인쇄 |

---

## 빠른 시작

### 사전 요구사항

- [Docker](https://docs.docker.com/get-docker/) 24.0+
- [Docker Compose](https://docs.docker.com/compose/) v2.0+

### 실행

```bash
# 1. 저장소 클론
git clone https://github.com/mesmeriz2/PDF-compressor.git
cd PDF-compressor

# 2. 전체 스택 빌드 및 시작 (별도 설정 불필요)
docker compose up -d --build

# 3. 로그 확인
docker compose logs -f
```

### 접속

| 서비스 | URL |
|--------|-----|
| **백엔드 API** | http://localhost:8106/api |
| **Nginx 통합** | http://localhost:8106 |
| **API 문서** | http://localhost:8106/docs |

### 중지

```bash
docker compose down

# 볼륨(데이터)까지 삭제
docker compose down -v
```

---

## 환경 변수

설정값은 `docker-compose.yml`의 `environment:` 블록에서 주입됩니다.
**`.env` 파일은 사용하지 않습니다** — compose에 `${VAR}` 치환도 `env_file:`도 없고,
이미지 안에도 `.env`가 들어가지 않습니다. 값을 바꾸려면 `docker-compose.yml`을 수정하세요.

`env.example`은 설정 가능한 항목과 기본값을 모아둔 **참조표**입니다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `REDIS_HOST` | `redis` | Redis 호스트명 |
| `REDIS_PORT` | `6379` | Redis 포트 |
| `MAX_UPLOAD_SIZE_MB` | `512` | 파일당 최대 업로드 크기 (MB) |
| `MAX_FILES_PER_BATCH` | `20` | 배치당 최대 파일 수 |
| `WORKER_CONCURRENCY` | `1` | Celery 동시 작업 수 |
| `RETENTION_HOURS` | `24` | 압축 파일 보관 시간 |
| `SYNC_COMPRESS_TIMEOUT_SECONDS` | `300` | `/api/compress`가 결과를 기다리는 상한 (초) |
| `ENABLE_DEDUPLICATION` | `true` | 동일 파일+옵션 결과 재사용 |
| `ENABLE_ENGINE_FALLBACK` | `true` | 엔진 자동 폴백 |
| `LOG_LEVEL` | `WARNING` | 로그 레벨 |
| `CORS_ORIGINS` | _(콤마 구분)_ | 허용 CORS 출처 |
| `ENABLE_ANTIVIRUS` | `false` | ClamAV 스캔 활성화 (별도 clamav 서비스 필요) |

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/compress` | **PDF 하나를 보내고 압축 결과를 바로 받음 (서버 간 연동)** |
| `POST` | `/api/upload` | PDF 업로드 및 압축 작업 등록 (비동기) |
| `GET` | `/api/jobs/{id}` | 작업 상태 조회 |
| `GET` | `/api/jobs/{id}/stream` | 작업 상태 SSE 스트림 |
| `GET` | `/api/jobs/{id}/download` | 압축 파일 다운로드 |
| `POST` | `/api/jobs/batch/download` | 여러 파일 ZIP 다운로드 |
| `POST` | `/api/jobs/{id}/cancel` | 작업 취소 |
| `DELETE` | `/api/jobs/{id}` | 작업 및 파일 삭제 |
| `GET` | `/api/healthz` | 헬스체크 |
| `GET` | `/api/readyz` | 준비 상태 확인 |

### 다른 서비스에서 호출하기

`POST /api/compress`는 PDF를 받아 압축된 PDF를 **응답 본문으로 그대로** 돌려줍니다.
옵션은 전부 생략 가능하며, 생략하면 기본값(`ebook` 프리셋 / `ghostscript` 엔진 / 메타데이터 보존)으로 처리합니다.

```bash
# 기본 옵션
curl -X POST http://localhost:8106/api/compress \
  -F "file=@input.pdf" \
  -o output.pdf

# 옵션 지정
curl -X POST http://localhost:8106/api/compress \
  -F "file=@input.pdf" \
  -F "preset=screen" \
  -F "engine=pikepdf" \
  -F "preserve_metadata=false" \
  -o output.pdf
```

```python
import requests

with open("input.pdf", "rb") as f:
    r = requests.post("http://localhost:8106/api/compress", files={"file": f})

r.raise_for_status()
open("output.pdf", "wb").write(r.content)
print(r.headers["X-Original-Size"], "→", r.headers["X-Compressed-Size"])
```

응답 헤더로 `X-Job-Id`, `X-Original-Size`, `X-Compressed-Size`, `X-Compression-Ratio`를 함께 줍니다.

`SYNC_COMPRESS_TIMEOUT_SECONDS`(기본 300초) 안에 끝나지 않으면 **202**와 함께
`job_id`·`download_url`을 돌려주므로, 완료 후 `GET /api/jobs/{id}/download`로 받아가면 됩니다.
동일한 파일+옵션이 이미 처리돼 있으면 압축 없이 즉시 결과를 반환합니다.

전체 API 명세: http://localhost:8106/docs (Swagger UI)

---

## 프로젝트 구조

```
PDF-compressor/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── upload.py       # 파일 업로드 엔드포인트
│   │   │   ├── jobs.py         # 작업 관리 엔드포인트
│   │   │   └── health.py       # 헬스체크 엔드포인트
│   │   ├── core/
│   │   │   ├── config.py       # 환경 설정 (pydantic-settings)
│   │   │   └── schemas.py      # Pydantic 응답 스키마
│   │   ├── models/
│   │   │   ├── job.py          # SQLAlchemy Job 모델
│   │   │   └── database.py     # SQLite 엔진 설정
│   │   ├── services/
│   │   │   ├── compression_engine.py  # 압축 엔진 (전략 패턴)
│   │   │   └── file_service.py        # 파일 처리 유틸리티
│   │   ├── workers/
│   │   │   ├── celery_app.py   # Celery 앱 설정
│   │   │   └── tasks.py        # compress / cleanup 태스크
│   │   └── main.py             # FastAPI 앱 진입점
│   ├── tests/                  # pytest 테스트
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── worker-entrypoint.sh    # Celery worker + 내장 Beat
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   └── page.tsx        # 메인 페이지 (업로드 + SSE 구독)
│   │   ├── components/
│   │   │   ├── FileUploader.tsx # 드래그 앤 드롭 업로더
│   │   │   ├── JobCard.tsx      # 작업 카드 (진행률/다운로드)
│   │   │   └── SettingsPanel.tsx# 프리셋/엔진 설정
│   │   └── lib/
│   │       ├── api.ts           # API 클라이언트
│   │       ├── sse.ts           # SSE 구독 헬퍼
│   │       └── constants.ts     # 프리셋/엔진/한도 상수
│   └── Dockerfile
├── docker-compose.yml
├── nginx.conf
└── env.example
```

---

## 개발

### 백엔드 테스트

```bash
cd backend
pip install -r requirements.txt -r requirements-test.txt

# 전체 테스트
pytest

# 빠른 테스트만 (slow/integration 제외)
pytest -m "not slow and not integration"

# 특정 파일
pytest tests/test_api.py
```

### 프론트엔드 로컬 개발

```bash
cd frontend
npm install

# 개발 서버 (백엔드가 별도 실행 중이어야 함)
npm run dev

# 빌드
npm run build

# 린트
npm run lint
```

### 코드 변경 후 재빌드

```bash
docker compose up -d --build
```

---

## 기술 스택

| 분류 | 기술 |
|------|------|
| 프론트엔드 | Next.js 14, TypeScript, Tailwind CSS, react-dropzone |
| 백엔드 | FastAPI, SQLAlchemy, Pydantic v2, SQLite |
| 태스크 큐 | Celery 5, Redis 7 |
| PDF 압축 | Ghostscript, pikepdf |
| 인프라 | Docker, Docker Compose, Nginx |

---

## 라이선스

[MIT License](LICENSE)
