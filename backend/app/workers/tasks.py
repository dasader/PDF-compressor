"""Celery 작업"""
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
from celery import Task
from app.workers.celery_app import celery_app
from app.core.config import settings
from app.models.database import SessionLocal, db_session
from app.models.job import Job, JobStatus, CompressionPreset
from app.services.compression_engine import get_engine
from app.services.file_service import FileService

logger = logging.getLogger(__name__)


class CallbackTask(Task):
    """진행률 콜백 지원 작업"""
    
    def update_progress(self, job_id: str, progress: float, eta_seconds: int = None):
        """작업 진행률 업데이트"""
        try:
            with db_session() as db:
                job = db.query(Job).filter(Job.id == job_id).first()
                if job:
                    job.progress = min(progress, 1.0)
                    if eta_seconds is not None:
                        job.eta_seconds = eta_seconds
                    logger.info(f"작업 {job_id} 진행률: {progress * 100:.1f}%")
        except Exception as e:
            logger.error(f"진행률 업데이트 실패: {e}")


@celery_app.task(bind=True, base=CallbackTask, max_retries=settings.TASK_MAX_RETRIES)
def compress_pdf_task(self, job_id: str) -> Dict[str, Any]:
    """
    PDF 압축 작업

    Args:
        job_id: 작업 ID

    Returns:
        작업 결과
    """
    try:
        with db_session() as db:
            # 작업 정보 가져오기
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                raise ValueError(f"작업을 찾을 수 없습니다: {job_id}")

            logger.info(f"작업 시작: {job_id} - {job.filename}")

            # 상태 업데이트
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)
            job.celery_task_id = self.request.id
            db.flush()

            # 파일 경로
            input_path = os.path.join(settings.UPLOAD_DIR, job.filename)
            output_filename = f"compressed_{job.filename}"
            output_path = os.path.join(settings.RESULT_DIR, output_filename)

            # 입력 파일 존재 확인
            if not os.path.exists(input_path):
                raise FileNotFoundError(f"입력 파일이 없습니다: {input_path}")

            # PDF 유효성 검사
            if not FileService.validate_pdf(input_path):
                raise ValueError("유효하지 않은 PDF 파일입니다")

            # 안티바이러스 스캔 (설정된 경우에만)
            if settings.ENABLE_ANTIVIRUS:
                if not FileService.scan_antivirus(input_path):
                    raise ValueError("바이러스가 감지된 파일입니다")

            # 진행률 콜백
            def progress_callback(progress: float):
                self.update_progress(job_id, progress)

            # PDF 정보 추출
            self.update_progress(job_id, 0.1)
            engine_impl = get_engine(job.engine)
            pdf_info = engine_impl.get_pdf_info(input_path)

            job.page_count = pdf_info.get('page_count', 0)
            job.image_count = pdf_info.get('image_count', 0)
            db.flush()

            logger.info(f"PDF 정보: {pdf_info}")

            # 암호화된 PDF 확인
            if pdf_info.get('encrypted'):
                raise ValueError("암호화된 PDF는 지원하지 않습니다")

            # 압축 옵션 구성
            options = {}
            if job.custom_options:
                import json
                options = json.loads(job.custom_options)

            # 압축 실행
            self.update_progress(job_id, 0.2)
            logger.info(f"압축 시작: engine={job.engine}, preset={job.preset}")

            engine_impl.compress(
                input_path=input_path,
                output_path=output_path,
                preset=job.preset,
                options=options,
                progress_callback=progress_callback,
            )

            # 결과 확인
            if not os.path.exists(output_path):
                raise RuntimeError("압축된 파일이 생성되지 않았습니다")

            compressed_size = os.path.getsize(output_path)

            # 압축률 계산
            compression_ratio = compressed_size / job.original_size if job.original_size > 0 else 1.0

            logger.info(f"압축 완료: {job.original_size} -> {compressed_size} bytes (ratio: {compression_ratio:.2%})")

            # 작업 완료
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc)
            job.compressed_size = compressed_size
            job.compression_ratio = compression_ratio
            job.result_file = output_filename
            job.progress = 1.0
            job.expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.RETENTION_HOURS)

            return {
                'success': True,
                'job_id': job_id,
                'compressed_size': compressed_size,
                'compression_ratio': compression_ratio,
            }

    except Exception as e:
        logger.error(f"작업 실패: {job_id} - {e}", exc_info=True)
        retry_countdown = None
        try:
            with db_session() as db:
                job = db.query(Job).filter(Job.id == job_id).first()
                if job:
                    job.retry_count += 1
                    if job.retry_count < settings.TASK_MAX_RETRIES:
                        logger.info(f"작업 재시도: {job_id} ({job.retry_count}/{settings.TASK_MAX_RETRIES})")
                        retry_countdown = 60 * (2 ** job.retry_count)
                    else:
                        # 최대 재시도 초과
                        job.status = JobStatus.FAILED
                        job.error_message = str(e)
                        job.completed_at = datetime.now(timezone.utc)
        except Exception as inner:
            logger.error(f"재시도 레코드 업데이트 실패: {inner}")

        if retry_countdown is not None:
            raise self.retry(exc=e, countdown=retry_countdown)
        raise


@celery_app.task
def cleanup_old_files_task():
    """오래된 파일 정리 작업"""
    logger.info("파일 정리 작업 시작")
    try:
        FileService.cleanup_old_files()
        
        # DB에서도 만료된 작업 정리
        db = SessionLocal()
        cutoff_time = datetime.now(timezone.utc)
        expired_jobs = db.query(Job).filter(
            Job.expires_at < cutoff_time,
            Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED])
        ).all()
        
        for job in expired_jobs:
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
        db.close()
        
        logger.info(f"정리 완료: {len(expired_jobs)}개 작업 삭제")
        
    except Exception as e:
        logger.error(f"파일 정리 실패: {e}")


# 주기적 정리 작업 스케줄링
celery_app.conf.beat_schedule = {
    'cleanup-every-hour': {
        'task': 'app.workers.tasks.cleanup_old_files_task',
        'schedule': 3600.0 * settings.CLEANUP_INTERVAL_HOURS,
    },
}


















