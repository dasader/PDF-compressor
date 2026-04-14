"""업로드 API"""
import os
import uuid
import logging
import aiofiles
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.schemas import UploadResponse, CompressionOptions, JobResponse
from app.models.database import get_db
from app.models.job import Job, JobStatus, CompressionPreset
from app.services.file_service import FileService
from app.workers.tasks import compress_pdf_task
import hashlib as _hashlib
from app.core.redis_client import redis_client
from redis.exceptions import LockError

router = APIRouter()


def _options_hash(preset, engine, preserve_metadata, preserve_ocr) -> str:
    key = f"{preset}|{engine}|{preserve_metadata}|{preserve_ocr}".encode()
    return _hashlib.sha256(key).hexdigest()[:16]
logger = logging.getLogger(__name__)


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
    
    # 파일 개수 확인
    if len(files) > settings.MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"최대 {settings.MAX_FILES_PER_BATCH}개 파일까지 업로드 가능합니다"
        )
    
    # 디렉토리 생성
    settings.ensure_directories()
    
    job_ids = []
    
    for upload_file in files:
        try:
            # 파일명 정리
            original_filename = FileService.sanitize_filename(upload_file.filename)
            
            # 고유 파일명 생성
            file_id = str(uuid.uuid4())
            filename = f"{file_id}.pdf"
            file_path = os.path.join(settings.UPLOAD_DIR, filename)
            
            # 스트리밍 저장 + 해시 단일 패스
            logger.info(f"파일 저장 시작: {original_filename}")
            file_size, file_hash = await FileService.save_upload_file_with_hash(
                upload_file,
                file_path,
                max_size=settings.max_upload_size_bytes,
            )

            # PDF 유효성 검사
            if not FileService.validate_pdf(file_path):
                os.remove(file_path)
                raise HTTPException(status_code=400, detail=f"유효하지 않은 PDF: {original_filename}")

            # 안티바이러스 스캔
            if settings.ENABLE_ANTIVIRUS:
                if not FileService.scan_antivirus(file_path):
                    os.remove(file_path)
                    raise HTTPException(status_code=400, detail=f"바이러스 감지: {original_filename}")

            # dedup: Redis 분산 락으로 경합 보호
            reused = False
            if settings.ENABLE_DEDUPLICATION:
                opt_hash = _options_hash(preset, engine, preserve_metadata, preserve_ocr)
                lock_key = f"dedup:{file_hash}:{opt_hash}"
                try:
                    with redis_client.lock(lock_key, timeout=5, blocking_timeout=10):
                        existing_job = db.query(Job).filter(
                            Job.file_hash == file_hash,
                            Job.status == JobStatus.COMPLETED,
                            Job.expires_at > datetime.now(timezone.utc),
                            Job.preset == preset,
                            Job.engine == engine,
                            Job.preserve_metadata == preserve_metadata,
                            Job.preserve_ocr == preserve_ocr,
                        ).first()

                        if existing_job and existing_job.result_file:
                            result_path = os.path.join(settings.RESULT_DIR, existing_job.result_file)
                            if os.path.exists(result_path):
                                logger.info(f"중복 감지, 기존 결과 재사용: {file_hash}")
                                new_job = Job(
                                    id=file_id,
                                    user_session=user_session,
                                    filename=filename,
                                    original_filename=original_filename,
                                    file_hash=file_hash,
                                    original_size=file_size,
                                    compressed_size=existing_job.compressed_size,
                                    compression_ratio=existing_job.compression_ratio,
                                    page_count=existing_job.page_count,
                                    image_count=existing_job.image_count,
                                    preset=preset,
                                    engine=engine,
                                    preserve_metadata=preserve_metadata,
                                    preserve_ocr=preserve_ocr,
                                    status=JobStatus.COMPLETED,
                                    result_file=existing_job.result_file,
                                    progress=1.0,
                                    created_at=datetime.now(timezone.utc),
                                    completed_at=datetime.now(timezone.utc),
                                    expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.RETENTION_HOURS),
                                )
                                db.add(new_job)
                                db.commit()
                                job_ids.append(file_id)
                                reused = True
                except LockError:
                    logger.warning(f"dedup 락 획득 실패, 새 작업으로 진행: {file_hash}")
                except Exception as e:
                    logger.error(f"dedup 처리 중 오류, 새 작업으로 진행: {e}")

            if reused:
                continue

            # 일반 처리: 신규 Job + Celery dispatch
            job = Job(
                id=file_id,
                user_session=user_session,
                filename=filename,
                original_filename=original_filename,
                file_hash=file_hash,
                original_size=file_size,
                preset=preset,
                engine=engine,
                preserve_metadata=preserve_metadata,
                preserve_ocr=preserve_ocr,
                status=JobStatus.QUEUED,
                created_at=datetime.now(timezone.utc),
            )
            db.add(job)
            db.commit()

            task = compress_pdf_task.delay(file_id)
            job.celery_task_id = task.id
            db.commit()

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


@router.post("/upload-chunk")
async def upload_chunk(
    file_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    chunk: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    청크 단위 업로드 (대용량 파일용)
    
    - **file_id**: 파일 고유 ID
    - **chunk_index**: 청크 인덱스 (0부터 시작)
    - **total_chunks**: 전체 청크 수
    - **chunk**: 청크 데이터
    """
    
    try:
        # 임시 디렉토리에 청크 저장
        chunk_dir = os.path.join(settings.TEMP_DIR, file_id)
        os.makedirs(chunk_dir, exist_ok=True)
        
        chunk_path = os.path.join(chunk_dir, f"chunk_{chunk_index}")
        
        # 청크 저장
        async with aiofiles.open(chunk_path, 'wb') as f:
            content = await chunk.read()
            await f.write(content)
        
        logger.info(f"청크 저장: {file_id} - {chunk_index}/{total_chunks}")
        
        # 마지막 청크인 경우 병합
        if chunk_index == total_chunks - 1:
            # 모든 청크 존재 여부 확인
            missing = [i for i in range(total_chunks) if not os.path.exists(os.path.join(chunk_dir, f"chunk_{i}"))]
            if missing:
                raise HTTPException(status_code=400, detail=f"누락된 청크: {missing}")

            final_path = os.path.join(settings.UPLOAD_DIR, f"{file_id}.pdf")

            # 청크 병합
            with open(final_path, 'wb') as final_file:
                for i in range(total_chunks):
                    chunk_file = os.path.join(chunk_dir, f"chunk_{i}")
                    with open(chunk_file, 'rb') as cf:
                        final_file.write(cf.read())
            
            # 임시 디렉토리 삭제
            import shutil
            shutil.rmtree(chunk_dir)
            
            logger.info(f"파일 병합 완료: {file_id}")
            
            return {"status": "completed", "file_id": file_id}
        
        return {"status": "chunk_received", "chunk_index": chunk_index}
        
    except Exception as e:
        logger.error(f"청크 업로드 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
