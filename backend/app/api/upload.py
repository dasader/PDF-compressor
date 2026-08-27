"""업로드 API"""
import os
import uuid
import hashlib
import logging
from datetime import timedelta
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.schemas import JobResponse, UploadFailure, UploadResponse
from app.core.redis_client import redis_client
from app.models.database import get_db
from app.models.job import Job, JobStatus, CompressionPreset, utcnow
from app.services.file_service import FileService
from app.workers.tasks import compress_pdf_task
from redis.exceptions import LockError

router = APIRouter()
logger = logging.getLogger(__name__)


def _options_hash(base: dict) -> str:
    key = f"{base['preset']}|{base['engine']}|{base['preserve_metadata']}".encode()
    return hashlib.sha256(key).hexdigest()[:16]


def _reuse_completed_result(db: Session, base: dict) -> Optional[Job]:
    """같은 파일+옵션의 완료된 결과가 있으면 하드링크로 재사용해 Job을 만든다.

    결과 파일을 새 Job 이름으로 하드링크하기 때문에, 원본 Job이 정리되어도
    재사용한 Job의 결과는 살아남는다 (경로만 공유하면 정리 작업과 경합한다).
    Redis 분산 락으로 동시 업로드 경합을 막는다. 재사용했으면 새 Job.
    """
    lock_key = f"dedup:{base['file_hash']}:{_options_hash(base)}"
    try:
        with redis_client.lock(lock_key, timeout=5, blocking_timeout=10):
            existing = db.query(Job).filter(
                Job.file_hash == base['file_hash'],
                Job.status == JobStatus.COMPLETED,
                Job.expires_at > utcnow(),
                Job.preset == base['preset'],
                Job.engine == base['engine'],
                Job.preserve_metadata == base['preserve_metadata'],
            ).first()

            if not (existing and existing.result_exists):
                return None

            now = utcnow()
            job = Job(
                **base,
                compressed_size=existing.compressed_size,
                compression_ratio=existing.compression_ratio,
                page_count=existing.page_count,
                image_count=existing.image_count,
                status=JobStatus.COMPLETED,
                result_file=f"compressed_{base['filename']}",
                progress=1.0,
                completed_at=now,
                expires_at=now + timedelta(hours=settings.RETENTION_HOURS),
            )
            os.link(existing.result_path, job.result_path)

            db.add(job)
            db.commit()
            logger.info(f"중복 감지, 기존 결과 재사용: {base['file_hash']}")
            return job
    except LockError:
        logger.warning(f"dedup 락 획득 실패, 새 작업으로 진행: {base['file_hash']}")
    except Exception as e:
        logger.error(f"dedup 처리 중 오류, 새 작업으로 진행: {e}")
    return None


@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    files: List[UploadFile] = File(...),
    preset: CompressionPreset = Form(CompressionPreset.EBOOK),
    engine: Optional[str] = Form("ghostscript"),
    preserve_metadata: bool = Form(True),
    user_session: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    PDF 파일 업로드 및 압축 작업 등록

    - **files**: 업로드할 PDF 파일들 (최대 20개)
    - **preset**: 압축 프리셋 (screen/ebook/printer/prepress)
    - **engine**: 압축 엔진 (ghostscript/qpdf/pikepdf)
    - **preserve_metadata**: 메타데이터 보존 여부
    - **user_session**: 사용자 세션 ID (옵션)

    파일 단위로 실패를 모아 `failed`로 돌려준다. 전부 실패한 경우에만 400.
    """

    if len(files) > settings.MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"최대 {settings.MAX_FILES_PER_BATCH}개 파일까지 업로드 가능합니다"
        )

    created: List[Job] = []
    queued: List[tuple] = []   # (job, task_id) — 커밋 후 한꺼번에 디스패치
    failed: List[UploadFailure] = []

    for upload_file in files:
        original_filename = FileService.sanitize_filename(upload_file.filename)
        file_id = str(uuid.uuid4())
        filename = f"{file_id}.pdf"
        file_path = os.path.join(settings.UPLOAD_DIR, filename)

        try:
            logger.info(f"파일 저장 시작: {original_filename}")
            file_size, file_hash = await FileService.save_upload_file_with_hash(
                upload_file, file_path, max_size=settings.max_upload_size_bytes,
            )

            if not FileService.validate_pdf(file_path):
                raise ValueError("유효하지 않은 PDF 파일입니다")

            if not FileService.scan_antivirus(file_path):
                raise ValueError("바이러스가 감지되었습니다")

            base = dict(
                id=file_id,
                user_session=user_session,
                filename=filename,
                original_filename=original_filename,
                file_hash=file_hash,
                original_size=file_size,
                created_at=utcnow(),
                preset=preset,
                engine=engine,
                preserve_metadata=preserve_metadata,
            )

            # 락 획득(최대 10초)과 커밋은 blocking이라 이벤트 루프 밖에서 돌린다
            reused = None
            if settings.ENABLE_DEDUPLICATION:
                reused = await run_in_threadpool(_reuse_completed_result, db, base)

            if reused is not None:
                # 재사용했으면 방금 저장한 원본은 압축될 일이 없다
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                created.append(reused)
                continue

            # task_id를 미리 정해두면 커밋 한 번으로 Job 저장이 끝난다
            job = Job(**base, status=JobStatus.QUEUED, celery_task_id=str(uuid.uuid4()))
            db.add(job)
            created.append(job)
            queued.append((job, job.celery_task_id))

        except Exception as e:
            logger.error(f"업로드 처리 실패: {upload_file.filename} - {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            failed.append(UploadFailure(filename=original_filename, error=str(e)))

    if queued:
        await run_in_threadpool(db.commit)
        # 커밋 이후에 디스패치해야 워커가 행을 확실히 볼 수 있다
        for job, task_id in queued:
            compress_pdf_task.apply_async(args=[job.id], task_id=task_id)
            logger.info(f"작업 등록: {job.id}")

    if failed and not created:
        raise HTTPException(status_code=400, detail=failed[0].error)

    message = f"{len(created)}개 파일 업로드 완료"
    if failed:
        message += f", {len(failed)}개 실패"

    return UploadResponse(
        jobs=[JobResponse.model_validate(job) for job in created],
        failed=failed,
        message=message,
    )
