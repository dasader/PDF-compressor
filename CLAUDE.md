# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A large-scale PDF compression web application. Users upload PDFs via the Next.js frontend, the FastAPI backend enqueues Celery tasks via Redis, and a Celery worker runs the actual compression using Ghostscript, qpdf, or pikepdf. Compressed files are retained for 24 hours and cleaned up by an embedded Celery Beat scheduler (running inside the worker container).

## Running the Application

The entire stack is Docker-based. Copy `env.example` to `.env` before starting.

```bash
# Start all services (Redis, backend, worker (with embedded Beat), frontend, nginx)
docker compose up -d

# View logs
docker compose logs -f backend
docker compose logs -f worker

# Rebuild after code changes
docker compose up -d --build
```

Service ports:
- Frontend: `http://localhost:3001`
- Backend API: `http://localhost:8001`
- Nginx (unified entry): `http://localhost:8082`

## Backend Development

```bash
# Run tests (from backend/)
cd backend
pytest

# Run a single test file
pytest tests/test_api.py

# Run a single test function
pytest tests/test_api.py::test_function_name

# Run only fast tests (exclude slow/integration)
pytest -m "not slow and not integration"
```

Tests use `pytest.ini` in `backend/`. Test markers: `slow`, `integration`.

## Frontend Development

```bash
# Install deps (from frontend/)
cd frontend
npm install

# Local dev server (requires backend running separately or via Docker)
npm run dev

# Build for production
npm run build

# Lint
npm run lint
```

`NEXT_PUBLIC_API_URL` defaults to empty string (relative URLs), so API calls go through the same host via nginx rewrites in production or `next.config.js` rewrites in dev.

## Architecture

### Request Flow
1. Browser → Next.js frontend (port 3001 or via nginx at 8082)
2. `POST /api/upload` → FastAPI backend saves file to `/data/uploads/`, creates a `Job` record in SQLite, enqueues `compress_pdf_task` to Redis/Celery
3. Celery worker picks up the task, runs the compression engine, writes result to `/data/results/`
4. Frontend subscribes to `GET /api/jobs/{id}/stream` (SSE). Backend emits a snapshot on connect and forwards worker-published progress/status events from Redis pub/sub channel `job:{id}`
5. On completion, user downloads via `GET /api/jobs/{id}/download`

### Backend (`backend/app/`)
- **`main.py`**: FastAPI app setup — CORS, lifespan (DB init + directory creation), router mounting
- **`core/config.py`**: All configuration via `pydantic-settings`. Reads from `.env`. Key instance: `settings`
- **`core/redis_client.py`**: Shared Redis client used for distributed locks (dedup) and pub/sub (SSE)
- **`models/job.py`**: SQLAlchemy `Job` model with `JobStatus` and `CompressionPreset` enums; composite indexes `(user_session, status)` and `(expires_at, created_at)`
- **`models/database.py`**: SQLite engine with WAL + pragma tuning (cache 32MB, mmap 128MB, busy_timeout 30s), `SessionLocal`, and `db_session()` context manager (auto-commit/rollback/close). DB file lives inside the Docker volume at `/data/`
- **`api/upload.py`**: Single-pass save + SHA-256 hash; Redis distributed-lock-protected dedup; Celery task dispatch
- **`api/jobs.py`**: Job CRUD, SSE stream endpoint (`/jobs/{id}/stream`), per-file download, batch ZIP, cancellation via Celery revoke
- **`services/file_service.py`**: `save_upload_file_with_hash` (single-pass), PDF validation, filename sanitization
- **`services/compression_engine.py`**: Strategy pattern — `GhostscriptEngine`, `QPDFEngine`, `PikePDFEngine` all extend `CompressionEngine`. `get_engine()` handles engine lookup and fallback chain (ghostscript → qpdf → pikepdf)
- **`workers/tasks.py`**: `compress_pdf_task` with DB progress + Redis pub/sub publishing; `cleanup_old_files_task` runs hourly via embedded Beat and batches-and-paginates expired jobs (uploads + results deleted together)

### Compression Presets
| Preset | DPI | JPEG Quality | Use Case |
|--------|-----|-------------|----------|
| screen | 72 | 30 | Maximum compression |
| ebook | 150 | 60 | Default — balanced |
| printer | 300 | 80 | Print quality |
| prepress | 300 | 90 | High fidelity |

### Frontend (`frontend/src/`)
- **`app/page.tsx`**: Single-page app — manages job state, SSE subscription per active job, upload flow
- **`components/FileUploader.tsx`**: Drag-and-drop using `react-dropzone`
- **`components/JobCard.tsx`**: Per-job status card with progress bar, download/cancel/delete actions
- **`components/SettingsPanel.tsx`**: Preset, engine, and metadata options
- **`lib/api.ts`**: Axios client wrapper; all API calls; `Job` TypeScript interface mirrors the backend schema
- **`lib/sse.ts`**: `subscribeJob(jobId, onUpdate, onTerminal)` — EventSource wrapper that listens to `snapshot`/`update` events and auto-closes on terminal status

### Key Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `redis` | Redis hostname |
| `MAX_UPLOAD_SIZE_MB` | `512` | Per-file upload limit |
| `WORKER_CONCURRENCY` | `1` | Celery concurrent tasks per worker |
| `RETENTION_HOURS` | `24` | File expiry after compression |
| `ENABLE_DEDUPLICATION` | `true` | Reuse results for identical files+options |
| `ENABLE_ENGINE_FALLBACK` | `true` | Fallback to next engine if primary unavailable |
| `CORS_ORIGINS` | comma-separated list | Allowed CORS origins |

### Infrastructure Notes
- SQLite is used for job metadata (no external DB needed); WAL mode enabled; DB file stored in the shared `/data/` Docker volume
- Celery Beat runs **embedded** in the worker (`-B` flag); no separate beat container. Schedule stored at `/data/celerybeat-schedule`
- Redis runs without AOF/RDB persistence (queue/results are ephemeral) with `volatile-lru` eviction. Also serves as SSE pub/sub backbone on channels `job:{id}`
- `pikepdf` is always available (pure Python); `ghostscript` and `qpdf` require system binaries installed in the backend image (multi-stage build)
- Encrypted PDFs are rejected at both upload-time (worker validation) and task-time
- Deduplication uses Redis distributed locks on `dedup:{file_hash}:{options_hash}` to prevent race conditions during concurrent uploads of the same file
- Chunk upload endpoint (`POST /api/upload-chunk`) exists for large files but is not wired into the frontend UI
- Container memory limits (total ~3.0GB): redis 512M, backend 640M, worker 1.5G (embedded Beat), frontend 256M, nginx 64M. Worker auto-restarts when a child exceeds 1.2GB RSS
