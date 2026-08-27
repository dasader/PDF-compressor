"""업로드 API"""
import os
import uuid
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.schemas import UploadResponse
from app.core.redis_client import redis_client
from app.models.database import get_db
from app.models.job import Job, JobStatus, CompressionPreset
from app.services.file_service import FileService
from app.workers.tasks import compress_pdf_task
from redis.exceptions import LockError

router = APIRouter()
logger = logging.getLogger(__name__)


def _options_hash(preset, engine, preserve_metadata, preserve_ocr) -> str:
    key = f"{preset}|{engine}|{preserve_metadata}|{preserve_ocr}".encode()
    return hashlib.sha256(key).hexdigest()[:16]


def _reuse_completed_result(db: Session, file_hash: str, base_kwargs: dict, **opts) -> bool:
    """같은 파일+옵션의 완료된 결과가 있으면 재사용해 Job을 만든다.

    Redis 분산 락으로 동시 업로드 경합을 막는다. 재사용했으면 True.
    """
    lock_key = f"dedup:{file_hash}:{_options_hash(**opts)}"
    try:
        with redis_client.lock(lock_key, timeout=5, blocking_timeout=10):
            existing = db.query(Job).filter(
                Job.file_hash == file_hash,
                Job.status == JobStatus.COMPLETED,
                Job.expires_at > datetime.now(timezone.utc),
                Job.preset == opts['preset'],
                Job.engine == opts['engine'],
                Job.preserve_metadata == opts['preserve_metadata'],
                Job.preserve_ocr == opts['preserve_ocr'],
            ).first()

            if not (existing and existing.result_file and os.path.exists(existing.result_path)):
                return False

            logger.info(f"중복 감지, 기존 결과 재사용: {file_hash}")
            now = datetime.now(timezone.utc)
            db.add(Job(
                **base_kwargs,
                compressed_size=existing.compressed_size,
                compression_ratio=existing.compression_ratio,
                page_count=existing.page_count,
                image_count=existing.image_count,
                status=JobStatus.COMPLETED,
                result_file=existing.result_file,
                progress=1.0,
                completed_at=now,
                expires_at=now + timedelta(hours=settings.RETENTION_HOURS),
            ))
            db.commit()
            return True
    except LockError:
        logger.warning(f"dedup 락 획득 실패, 새 작업으로 진행: {file_hash}")
    except Exception as e:
        logger.error(f"dedup 처리 중 오류, 새 작업으로 진행: {e}")
    return False


@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    files: List[UploadFile] = File(...),
    preset: CompressionPreset = Form(CompressionPreset.EBOOK),
    engine: Optional[str] = Form("ghostscript"),
    preserve_metadata: bool = Form(True),
    preserve_ocr: bool = Form(True),
    user_session: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    PDF 파일 업로드 및 압축 작업 등록

    - **files**: 업로드할 PDF 파일들 (최대 20개)
    - **preset**: 압축 프리셋 (screen/ebook/printer/prepress)
    - **engine**: 압축 엔진 (ghostscript/qpdf/pikepdf)
    - **preserve_metadata**: 메타데이터 보존 여부
    - **preserve_ocr**: OCR 텍스트 레이어 보존 여부
    - **user_session**: 사용자 세션 ID (옵션)
    """

    if len(files) > settings.MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"최대 {settings.MAX_FILES_PER_BATCH}개 파일까지 업로드 가능합니다"
        )

    opts = dict(preset=preset, engine=engine,
                preserve_metadata=preserve_metadata, preserve_ocr=preserve_ocr)
    job_ids = []

    for upload_file in files:
        try:
            original_filename = FileService.sanitize_filename(upload_file.filename)
            file_id = str(uuid.uuid4())
            filename = f"{file_id}.pdf"
            file_path = os.path.join(settings.UPLOAD_DIR, filename)

            logger.info(f"파일 저장 시작: {original_filename}")
            file_size, file_hash = await FileService.save_upload_file_with_hash(
                upload_file,
                file_path,
                max_size=settings.max_upload_size_bytes,
            )

            if not FileService.validate_pdf(file_path):
                os.remove(file_path)
                raise HTTPException(status_code=400, detail=f"유효하지 않은 PDF: {original_filename}")

            if not FileService.scan_antivirus(file_path):
                os.remove(file_path)
                raise HTTPException(status_code=400, detail=f"바이러스 감지: {original_filename}")

            base_kwargs = dict(
                id=file_id,
                user_session=user_session,
                filename=filename,
                original_filename=original_filename,
                file_hash=file_hash,
                original_size=file_size,
                created_at=datetime.now(timezone.utc),
                **opts,
            )

            if settings.ENABLE_DEDUPLICATION and _reuse_completed_result(
                db, file_hash, base_kwargs, **opts
            ):
                job_ids.append(file_id)
                continue

            # task_id를 미리 정해두면 Job 저장이 커밋 한 번으로 끝난다
            task_id = str(uuid.uuid4())
            db.add(Job(**base_kwargs, status=JobStatus.QUEUED, celery_task_id=task_id))
            db.commit()

            compress_pdf_task.apply_async(args=[file_id], task_id=task_id)

            logger.info(f"작업 등록: {file_id} - {original_filename}")
            job_ids.append(file_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"업로드 처리 실패: {upload_file.filename} - {e}")
            raise HTTPException(status_code=500, detail=f"업로드 실패: {str(e)}")

    return UploadResponse(
        job_ids=job_ids,
        message=f"{len(job_ids)}개 파일 업로드 완료"
    )
