# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A large-scale PDF compression web application. Users upload PDFs via the Next.js frontend, the FastAPI backend enqueues Celery tasks via Redis, and a Celery worker runs the actual compression using Ghostscript, qpdf, or pikepdf. Compressed files are retained for 24 hours and cleaned up by a Celery Beat scheduler.

## Running the Application

The entire stack is Docker-based. Copy `env.example` to `.env` before starting.

```bash
# Start all services (Redis, backend, worker, beat, frontend, nginx)
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
4. Frontend polls `GET /api/jobs/{id}` every 2 seconds to update job status
5. On completion, user downloads via `GET /api/jobs/{id}/download`

### Backend (`backend/app/`)
- **`main.py`**: FastAPI app setup — CORS, lifespan (DB init + directory creation), router mounting
- **`core/config.py`**: All configuration via `pydantic-settings`. Reads from `.env`. Key instance: `settings`
- **`models/job.py`**: SQLAlchemy `Job` model with `JobStatus` and `CompressionPreset` enums
- **`models/database.py`**: SQLite engine and `SessionLocal`; DB file lives inside the Docker volume at `/data/`
- **`api/upload.py`**: Handles file upload, deduplication by SHA-256 hash, and Celery task dispatch
- **`api/jobs.py`**: Job CRUD, per-file download (`FileResponse`), batch ZIP download (`StreamingResponse`), job cancellation via Celery revoke
- **`services/compression_engine.py`**: Strategy pattern — `GhostscriptEngine`, `QPDFEngine`, `PikePDFEngine` all extend `CompressionEngine`. `get_engine()` handles engine lookup and fallback chain (ghostscript → qpdf → pikepdf)
- **`workers/tasks.py`**: `compress_pdf_task` Celery task with progress callbacks stored to DB; `cleanup_old_files_task` scheduled hourly by Celery Beat

### Compression Presets
| Preset | DPI | JPEG Quality | Use Case |
|--------|-----|-------------|----------|
| screen | 72 | 30 | Maximum compression |
| ebook | 150 | 60 | Default — balanced |
| printer | 300 | 80 | Print quality |
| prepress | 300 | 90 | High fidelity |

### Frontend (`frontend/src/`)
- **`app/page.tsx`**: Single-page app — manages job state, 2-second polling loop, upload flow
- **`components/FileUploader.tsx`**: Drag-and-drop using `react-dropzone`
- **`components/JobCard.tsx`**: Per-job status card with progress bar, download/cancel/delete actions
- **`components/SettingsPanel.tsx`**: Preset, engine, and metadata options
- **`lib/api.ts`**: Axios client wrapper; all API calls; `Job` TypeScript interface mirrors the backend schema

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
- SQLite is used for job metadata (no external DB needed); the DB file is stored in the shared `/data/` Docker volume
- The `beat` container runs Celery Beat for hourly file cleanup; the `worker` container runs the actual compression
- `pikepdf` is always available (pure Python); `ghostscript` and `qpdf` require system binaries installed in the Docker image
- Encrypted PDFs are rejected at both upload-time (worker validation) and task-time
- Chunk upload endpoint (`POST /api/upload-chunk`) exists for large files but is not wired into the frontend UI
