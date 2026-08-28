"""동기 압축 API — 다른 서비스가 PDF를 보내고 압축 결과를 바로 돌려받는다.

/api/upload은 작업을 큐에 넣고 job id만 주는 비동기 경로라 서버 간 연동에는
호출자가 폴링/SSE를 직접 붙여야 한다. 이 엔드포인트는 그 대기를 서버가 대신 한다.
"""
import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.api.upload import ingest_file
from app.core.config import settings
from app.models.database import db_session, get_db
from app.models.job import CompressionPreset, Job, JobStatus, TERMINAL_STATUSES
from app.workers.tasks import compress_pdf_task

router = APIRouter()
logger = logging.getLogger(__name__)


def _terminal_job(job_id: str) -> Optional[Job]:
    """작업이 끝났으면 그 Job을, 아직이면 None을 준다. 매번 새 세션으로 읽어야
    워커가 다른 커넥션으로 쓴 결과가 보인다."""
    with db_session() as db:
        job = db.query(Job).filter(Job.id == job_id).first()
        return job if job is not None and job.status in TERMINAL_STATUSES else None


async def _wait_for_result(job_id: str, timeout: float) -> Optional[Job]:
    """작업이 끝날 때까지 기다린다. 시간 안에 안 끝나면 None.

    ponytail: 0.5초 폴링. 압축이 보통 수 초 이상이라 이 지연은 묻힌다.
    더 즉각적인 응답이 필요해지면 SSE와 같은 Redis pub/sub 구독으로 바꾼다.
    """
    deadline = time.monotonic() + timeout
    while True:
        job = await run_in_threadpool(_terminal_job, job_id)
        if job is not None:
            return job
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.5)


@router.post(
    "/compress",
    responses={
        200: {"content": {"application/pdf": {}}, "description": "압축된 PDF"},
        202: {"description": "제한 시간 안에 끝나지 않음 — job_id로 나중에 받아간다"},
        400: {"description": "PDF가 아니거나 크기 초과"},
        422: {"description": "압축 실패"},
    },
)
async def compress_pdf(
    file: UploadFile = File(..., description="압축할 PDF 파일"),
    preset: CompressionPreset = Form(CompressionPreset.EBOOK),
    engine: str = Form("ghostscript"),
    preserve_metadata: bool = Form(True),
    db: Session = Depends(get_db),
):
    """
    PDF 하나를 보내고 압축 결과를 그대로 돌려받는다 (서버 간 연동용).

    옵션은 전부 생략 가능하며, 생략하면 기본값으로 처리한다:
    - **preset**: `ebook` (150 DPI, 균형) — screen/ebook/printer/prepress
    - **engine**: `ghostscript` (최대 압축, 이미지 화질 일부 저하) / `pikepdf` (무손실)
    - **preserve_metadata**: `true`

    응답:
    - **200** — 본문이 압축된 PDF. `X-Job-Id`, `X-Original-Size`, `X-Compressed-Size`,
      `X-Compression-Ratio` 헤더로 결과 정보를 함께 준다.
    - **202** — `SYNC_COMPRESS_TIMEOUT_SECONDS` 안에 끝나지 않은 경우.
      `job_id`와 `download_url`을 주므로 완료 후 `GET /api/jobs/{id}/download`로 받아간다.
    - **400** — 유효하지 않은 PDF이거나 업로드 크기 제한 초과.
    - **422** — 압축이 실패한 경우 (`detail`에 사유).

    동일한 파일+옵션이 이미 처리돼 있으면 압축 없이 즉시 결과를 돌려준다.
    """
    options = dict(preset=preset, engine=engine, preserve_metadata=preserve_metadata)

    try:
        job, task_id = await ingest_file(db, file, options)
    except Exception as e:
        logger.error(f"동기 압축 입력 처리 실패: {file.filename} - {e}")
        raise HTTPException(status_code=400, detail=str(e))

    job_id = job.id

    if task_id is None:
        # dedup으로 기존 결과를 재사용 — 기다릴 것이 없다
        logger.info(f"동기 압축: 기존 결과 재사용 {job_id}")
        return _pdf_response(job)

    await run_in_threadpool(db.commit)
    compress_pdf_task.apply_async(args=[job_id], task_id=task_id)
    logger.info(f"동기 압축 작업 등록: {job_id}")

    finished = await _wait_for_result(job_id, settings.SYNC_COMPRESS_TIMEOUT_SECONDS)

    if finished is None:
        logger.info(f"동기 압축 대기 시간 초과, job_id 반환: {job_id}")
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "status": "processing",
                "download_url": f"/api/jobs/{job_id}/download",
                "message": (
                    f"{settings.SYNC_COMPRESS_TIMEOUT_SECONDS}초 안에 끝나지 않았습니다. "
                    "download_url로 나중에 받아가세요."
                ),
            },
        )

    if finished.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=422,
            detail=finished.error_message or f"압축에 실패했습니다 (상태: {finished.status.value})",
        )

    return _pdf_response(finished)


def _pdf_response(job: Job) -> FileResponse:
    if not job.result_exists:
        raise HTTPException(status_code=422, detail="압축 결과 파일을 찾을 수 없습니다")

    return FileResponse(
        job.result_path,
        media_type="application/pdf",
        filename=job.download_name,
        headers={
            "X-Job-Id": job.id,
            "X-Original-Size": str(job.original_size),
            "X-Compressed-Size": str(job.compressed_size or 0),
            "X-Compression-Ratio": f"{job.compression_ratio or 1.0:.4f}",
        },
    )
