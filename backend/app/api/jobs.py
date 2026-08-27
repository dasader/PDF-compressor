"""작업 관리 API"""
import os
import io
import json
import asyncio
import logging
import zipfile
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from sse_starlette.sse import EventSourceResponse

from app.core.schemas import JobResponse
from app.core.redis_client import redis_client
from app.models.database import get_db
from app.models.job import Job, JobStatus, TERMINAL_STATUSES, TERMINAL_STATUS_VALUES
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

        if snapshot["status"] in TERMINAL_STATUS_VALUES:
            return

        pubsub = redis_client.pubsub()
        try:
            pubsub.subscribe(f"job:{job_id}")
            while True:
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    data = msg["data"]
                    if isinstance(data, (bytes, bytearray)):
                        data = data.decode()
                    yield {"event": "update", "data": data}
                    try:
                        parsed = json.loads(data)
                        if parsed.get("type") == "status" and parsed.get("status") in TERMINAL_STATUS_VALUES:
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


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job: Job = Depends(get_job_or_404)):
    """작업 상태 조회"""
    return job


@router.get("/jobs", response_model=List[JobResponse])
async def list_jobs(
    user_session: Optional[str] = None,
    status: Optional[JobStatus] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    작업 목록 조회

    - **user_session**: 사용자 세션 ID로 필터링 (옵션)
    - **status**: 작업 상태로 필터링 (옵션)
    - **limit**: 최대 결과 수
    - **offset**: 결과 오프셋
    """
    query = db.query(Job)

    if user_session:
        query = query.filter(Job.user_session == user_session)

    if status:
        query = query.filter(Job.status == status)

    return query.order_by(Job.created_at.desc()).limit(limit).offset(offset).all()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job: Job = Depends(get_job_or_404), db: Session = Depends(get_db)):
    """작업 취소"""
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=400, detail="이미 완료되거나 취소된 작업입니다")

    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=True)

    job.status = JobStatus.CANCELLED
    job.completed_at = datetime.now(timezone.utc)
    db.commit()

    logger.info(f"작업 취소: {job.id}")

    return {"status": "cancelled", "job_id": job.id}


@router.get("/jobs/{job_id}/download")
async def download_result(job: Job = Depends(get_job_or_404)):
    """압축된 PDF 다운로드"""
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="작업이 완료되지 않았습니다")

    if not job.result_file:
        raise HTTPException(status_code=404, detail="결과 파일이 없습니다")

    if job.expires_at and job.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="파일이 만료되었습니다")

    if not os.path.exists(job.result_path):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    # FileResponse가 filename을 RFC 5987(filename*=utf-8'')로 인코딩해준다
    return FileResponse(
        job.result_path,
        media_type="application/pdf",
        filename=job.download_name,
    )


@router.post("/jobs/batch/download")
async def download_batch(job_ids: List[str], db: Session = Depends(get_db)):
    """
    여러 작업 결과를 ZIP으로 다운로드

    - **job_ids**: 작업 ID 목록
    """
    jobs = db.query(Job).filter(
        Job.id.in_(job_ids),
        Job.status == JobStatus.COMPLETED
    ).all()

    if not jobs:
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for job in jobs:
            if job.result_file and os.path.exists(job.result_path):
                zip_file.write(job.result_path, job.download_name)

    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="compressed_files.zip"'}
    )


@router.delete("/jobs/{job_id}")
async def delete_job(job: Job = Depends(get_job_or_404), db: Session = Depends(get_db)):
    """작업 및 관련 파일 삭제"""
    delete_job_files(job)
    db.delete(job)
    db.commit()

    logger.info(f"작업 삭제: {job.id}")

    return {"status": "deleted", "job_id": job.id}
