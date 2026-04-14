"""작업 관리 API"""
import os
import logging
import asyncio
import json as _json
from typing import List, Optional
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.schemas import JobResponse
from app.core.redis_client import redis_client
from app.models.database import get_db
from app.models.job import Job, JobStatus
from app.workers.celery_app import celery_app

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, db: Session = Depends(get_db)):
    """Job 상태 변화를 SSE로 전달 (스냅샷 + Redis pub/sub)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")

    # 스냅샷 캡처 (엔드포인트 함수 스코프에서 세션이 닫히기 전에 값을 뽑아둔다)
    snapshot = {
        "job_id": job.id,
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "progress": job.progress,
        "eta_seconds": job.eta_seconds,
    }

    async def event_gen():
        yield {"event": "snapshot", "data": _json.dumps(snapshot)}

        if snapshot["status"] in ("completed", "failed", "cancelled"):
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
                        parsed = _json.loads(data)
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


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, db: Session = Depends(get_db)):
    """
    작업 상태 조회
    
    - **job_id**: 작업 ID
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    
    # 결과 URL 생성
    if job.status == JobStatus.COMPLETED and job.result_file:
        job.result_url = f"/api/jobs/{job_id}/download"
    
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
    
    jobs = query.order_by(Job.created_at.desc()).limit(limit).offset(offset).all()
    
    # 결과 URL 추가
    for job in jobs:
        if job.status == JobStatus.COMPLETED and job.result_file:
            job.result_url = f"/api/jobs/{job.id}/download"
    
    return jobs


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """
    작업 취소
    
    - **job_id**: 작업 ID
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    
    if job.status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]:
        raise HTTPException(status_code=400, detail="이미 완료되거나 취소된 작업입니다")
    
    # Celery 작업 취소
    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=True)
    
    # 상태 업데이트
    job.status = JobStatus.CANCELLED
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    
    logger.info(f"작업 취소: {job_id}")
    
    return {"status": "cancelled", "job_id": job_id}


@router.get("/jobs/{job_id}/download")
async def download_result(job_id: str, db: Session = Depends(get_db)):
    """
    압축된 PDF 다운로드
    
    - **job_id**: 작업 ID
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="작업이 완료되지 않았습니다")
    
    if not job.result_file:
        raise HTTPException(status_code=404, detail="결과 파일이 없습니다")
    
    # 만료 확인
    if job.expires_at and job.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="파일이 만료되었습니다")
    
    # 파일 경로
    file_path = os.path.join(settings.RESULT_DIR, job.result_file)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    
    # 다운로드 파일명
    download_name = f"compressed_{job.original_filename}"
    
    # RFC 5987에 따라 한글 파일명을 올바르게 인코딩
    # filename: ASCII 안전한 대체 이름
    # filename*: UTF-8로 인코딩된 원본 파일명
    encoded_filename = quote(download_name.encode('utf-8'))
    
    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename=download_name,
        headers={
            "Content-Disposition": f"attachment; filename=\"{download_name.encode('ascii', 'ignore').decode('ascii') or 'compressed.pdf'}\"; filename*=UTF-8''{encoded_filename}"
        }
    )


@router.post("/jobs/batch/download")
async def download_batch(job_ids: List[str], db: Session = Depends(get_db)):
    """
    여러 작업 결과를 ZIP으로 다운로드
    
    - **job_ids**: 작업 ID 목록
    """
    import zipfile
    import io
    
    jobs = db.query(Job).filter(
        Job.id.in_(job_ids),
        Job.status == JobStatus.COMPLETED
    ).all()
    
    if not jobs:
        raise HTTPException(status_code=404, detail="완료된 작업이 없습니다")
    
    # ZIP 파일 생성
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for job in jobs:
            if job.result_file:
                file_path = os.path.join(settings.RESULT_DIR, job.result_file)
                if os.path.exists(file_path):
                    archive_name = f"compressed_{job.original_filename}"
                    zip_file.write(file_path, archive_name)
    
    zip_buffer.seek(0)
    
    # ZIP 파일명도 RFC 5987에 따라 인코딩
    zip_filename = "compressed_files.zip"
    encoded_zip_filename = quote(zip_filename.encode('utf-8'))
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{zip_filename}\"; filename*=UTF-8''{encoded_zip_filename}"
        }
    )


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, db: Session = Depends(get_db)):
    """
    작업 및 관련 파일 삭제
    
    - **job_id**: 작업 ID
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    
    # 파일 삭제
    if job.filename:
        upload_path = os.path.join(settings.UPLOAD_DIR, job.filename)
        if os.path.exists(upload_path):
            os.remove(upload_path)
    
    if job.result_file:
        result_path = os.path.join(settings.RESULT_DIR, job.result_file)
        if os.path.exists(result_path):
            os.remove(result_path)
    
    # DB에서 삭제
    db.delete(job)
    db.commit()
    
    logger.info(f"작업 삭제: {job_id}")
    
    return {"status": "deleted", "job_id": job_id}











