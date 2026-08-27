"""작업 관리 API"""
import os
import json
import logging
import tempfile
import zipfile
from typing import List
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from datetime import timedelta
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.schemas import JobResponse
from app.core.redis_client import async_redis_client
from app.models.database import get_db
from app.models.job import Job, JobStatus, TERMINAL_STATUSES, utcnow
from app.services.file_service import delete_job_files
from app.workers.celery_app import celery_app

router = APIRouter()
logger = logging.getLogger(__name__)


def get_job_or_404(job_id: str, db: Session = Depends(get_db)) -> Job:
    """경로의 job_id로 Job을 찾거나 404."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    return job


@router.get("/jobs/{job_id}/stream")
async def stream_job(job: Job = Depends(get_job_or_404)):
    """Job 상태 변화를 SSE로 전달 (스냅샷 + Redis pub/sub)."""
    # 세션이 닫히기 전에 스냅샷 값을 뽑아둔다
    job_id = job.id
    snapshot = {"job_id": job_id, "status": job.status.value, "progress": job.progress}

    async def event_gen():
        yield {"event": "snapshot", "data": json.dumps(snapshot)}

        if snapshot["status"] in TERMINAL_STATUSES:
            return

        # 동기 클라이언트의 get_message는 이벤트 루프를 최대 1초씩 멈춘다 — 비동기 클라이언트를 쓴다
        pubsub = async_redis_client.pubsub()
        try:
            await pubsub.subscribe(f"job:{job_id}")
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not (msg and msg.get("type") == "message"):
                    continue

                data = msg["data"]
                if isinstance(data, (bytes, bytearray)):
                    data = data.decode()
                yield {"event": "update", "data": data}

                try:
                    parsed = json.loads(data)
                    if parsed.get("type") == "status" and parsed.get("status") in TERMINAL_STATUSES:
                        return
                except Exception:
                    pass
        finally:
            try:
                await pubsub.aclose()
            except Exception:
                pass

    return EventSourceResponse(event_gen())


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job: Job = Depends(get_job_or_404)):
    """작업 상태 조회"""
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job: Job = Depends(get_job_or_404), db: Session = Depends(get_db)):
    """작업 취소"""
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="이미 완료되거나 취소된 작업입니다")

    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=True)

    job.status = JobStatus.CANCELLED
    job.completed_at = utcnow()
    # expires_at을 채워야 정리 작업이 취소된 작업의 업로드 파일도 회수한다
    job.expires_at = job.completed_at + timedelta(hours=settings.RETENTION_HOURS)
    db.commit()

    logger.info(f"작업 취소: {job.id}")

    return {"status": "cancelled", "job_id": job.id}


@router.get("/jobs/{job_id}/download")
async def download_result(job: Job = Depends(get_job_or_404)):
    """압축된 PDF 다운로드"""
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="작업이 완료되지 않았습니다")

    if job.expires_at and job.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="파일이 만료되었습니다")

    if not job.result_exists:
        raise HTTPException(status_code=404, detail="결과 파일을 찾을 수 없습니다")

    # FileResponse가 filename을 RFC 5987(filename*=utf-8'')로 인코딩해준다
    return FileResponse(
        job.result_path,
        media_type="application/pdf",
        filename=job.download_name,
    )


# sync def — Starlette가 threadpool로 돌리므로 ZIP 생성이 이벤트 루프를 막지 않는다
@router.post("/jobs/batch/download")
def download_batch(job_ids: List[str], db: Session = Depends(get_db)):
    """
    여러 작업 결과를 ZIP으로 다운로드

    - **job_ids**: 작업 ID 목록
    """
    jobs = db.query(Job).filter(
        Job.id.in_(job_ids),
        Job.status == JobStatus.COMPLETED
    ).all()

    members = [(job.result_path, job.download_name) for job in jobs if job.result_exists]
    if not members:
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다")

    # 메모리에 통째로 올리면 512MB 파일 여러 개에 컨테이너가 죽는다 — 디스크에 만들고 스트리밍한다
    fd, zip_path = tempfile.mkstemp(suffix=".zip", dir=settings.TEMP_DIR)
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for path, arcname in members:
                zip_file.write(path, arcname)
    except Exception:
        os.remove(zip_path)
        raise

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="compressed_files.zip",
        background=BackgroundTask(os.remove, zip_path),
    )


@router.delete("/jobs/{job_id}")
async def delete_job(job: Job = Depends(get_job_or_404), db: Session = Depends(get_db)):
    """작업 및 관련 파일 삭제"""
    delete_job_files(job)
    db.delete(job)
    db.commit()

    logger.info(f"작업 삭제: {job.id}")

    return {"status": "deleted", "job_id": job.id}
