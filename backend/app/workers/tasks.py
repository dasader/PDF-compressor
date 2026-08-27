"""Celery 작업"""
import json
import logging
import os
from typing import Dict, Any
from app.workers.celery_app import celery_app
from app.core.config import settings
from app.core.redis_client import redis_client
from app.models.database import db_session
from app.models.job import Job, JobStatus, TERMINAL_STATUSES, expiry, utcnow
from app.services.compression_engine import get_engine, get_pdf_info
from app.services.file_service import FileService, delete_job_files

logger = logging.getLogger(__name__)


def _publish_job_event(job_id: str, payload: dict) -> None:
    """Redis 채널 'job:{id}'로 이벤트 발행. 실패해도 작업 진행에 영향 없음."""
    try:
        redis_client.publish(f"job:{job_id}", json.dumps(payload).encode())
    except Exception as e:
        logger.warning(f"publish 실패 (무시): {e}")


def _publish_status(job_id: str, status: str, **extra) -> None:
    _publish_job_event(job_id, {"job_id": job_id, "type": "status", "status": status, **extra})


def update_progress(job_id: str, progress: float) -> None:
    """작업 진행률 업데이트 (DB + SSE 발행)"""
    progress = min(progress, 1.0)
    try:
        with db_session() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.progress = progress
    except Exception as e:
        logger.error(f"진행률 업데이트 실패: {e}")
    _publish_job_event(job_id, {"job_id": job_id, "type": "progress", "progress": progress})


@celery_app.task(bind=True, max_retries=settings.TASK_MAX_RETRIES)
def compress_pdf_task(self, job_id: str) -> Dict[str, Any]:
    """
    PDF 압축 작업

    Args:
        job_id: 작업 ID

    Returns:
        작업 결과
    """
    try:
        # 1) 시작 표시. 압축은 최대 TASK_TIMEOUT_SECONDS만큼 걸리므로
        #    SQLite 쓰기 락을 그 구간 내내 쥐지 않도록 여기서 트랜잭션을 닫는다.
        with db_session() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                raise ValueError(f"작업을 찾을 수 없습니다: {job_id}")

            job.status = JobStatus.RUNNING
            job.started_at = utcnow()
            job.result_file = f"compressed_{job.filename}"

            # 세션이 닫히면 접근할 수 없으므로 압축에 필요한 값을 여기서 뽑아둔다
            input_path = job.upload_path
            output_path = job.result_path
            result_filename = job.result_file
            engine_name = job.engine
            preset = job.preset
            preserve_metadata = job.preserve_metadata
            original_size = job.original_size
            logger.info(f"작업 시작: {job_id} - {job.filename}")

        _publish_status(job_id, "running")

        # 2) 압축 구간 — DB 트랜잭션 없음
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"입력 파일이 없습니다: {input_path}")

        if not FileService.validate_pdf(input_path):
            raise ValueError("유효하지 않은 PDF 파일입니다")

        if not FileService.scan_antivirus(input_path):
            raise ValueError("바이러스가 감지된 파일입니다")

        update_progress(job_id, 0.1)
        pdf_info = get_pdf_info(input_path)
        logger.info(f"PDF 정보: {pdf_info}")

        if pdf_info.get('encrypted'):
            raise ValueError("암호화된 PDF는 지원하지 않습니다")

        update_progress(job_id, 0.3)
        logger.info(f"압축 시작: engine={engine_name}, preset={preset}")

        result = get_engine(engine_name).compress(
            input_path=input_path,
            output_path=output_path,
            preset=preset,
            preserve_metadata=preserve_metadata,
        )
        compressed_size = result['output_size']
        compression_ratio = compressed_size / original_size if original_size > 0 else 1.0
        update_progress(job_id, 0.9)

        logger.info(f"압축 완료: {original_size} -> {compressed_size} bytes (ratio: {compression_ratio:.2%})")

        # 3) 결과 기록 — 다시 짧은 트랜잭션
        with db_session() as db:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = JobStatus.COMPLETED
                job.completed_at = utcnow()
                job.compressed_size = compressed_size
                job.compression_ratio = compression_ratio
                job.result_file = result_filename
                job.page_count = pdf_info.get('page_count', 0)
                job.image_count = pdf_info.get('image_count', 0)
                job.progress = 1.0
                job.expires_at = expiry()

        # 결과가 확정된 뒤에는 원본을 들고 있을 이유가 없다 (보관 기간 동안 용량이 두 배가 된다)
        try:
            os.remove(input_path)
        except OSError as e:
            logger.warning(f"업로드 원본 삭제 실패: {input_path}: {e}")

        _publish_status(job_id, "completed",
                        compressed_size=compressed_size, compression_ratio=compression_ratio)
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
                    # 워커 유실 시 Celery의 self.request.retries는 0으로 리셋되므로
                    # 재시도 한도는 DB 컬럼으로 센다.
                    job.retry_count += 1
                    if job.retry_count < settings.TASK_MAX_RETRIES:
                        logger.info(f"작업 재시도: {job_id} ({job.retry_count}/{settings.TASK_MAX_RETRIES})")
                        retry_countdown = 60 * (2 ** job.retry_count)
                    else:
                        job.status = JobStatus.FAILED
                        job.error_message = str(e)
                        job.completed_at = utcnow()
                        job.result_file = None
                        # expires_at을 채워야 정리 작업이 실패한 작업의 업로드 파일도 회수한다
                        job.expires_at = expiry()
        except Exception as inner:
            logger.error(f"재시도 레코드 업데이트 실패: {inner}")

        if retry_countdown is not None:
            raise self.retry(exc=e, countdown=retry_countdown)
        _publish_status(job_id, "failed", error=str(e))
        raise


@celery_app.task
def cleanup_old_files_task():
    """만료된 Job과 연관된 업로드/결과 파일을 배치 단위로 정리한다."""
    logger.info("파일 정리 작업 시작")
    cutoff_time = utcnow()
    total_deleted = 0
    batch_size = 200

    try:
        while True:
            with db_session() as db:
                expired_jobs = (
                    db.query(Job)
                    .filter(
                        Job.expires_at < cutoff_time,
                        Job.status.in_(TERMINAL_STATUSES),
                    )
                    .limit(batch_size)
                    .all()
                )
                if not expired_jobs:
                    break

                for job in expired_jobs:
                    delete_job_files(job)
                    db.delete(job)
                total_deleted += len(expired_jobs)

        logger.info(f"정리 완료: {total_deleted}개 작업 삭제")
    except Exception as e:
        logger.error(f"파일 정리 실패: {e}", exc_info=True)


# 주기적 정리 작업 스케줄링
celery_app.conf.beat_schedule = {
    'cleanup-every-hour': {
        'task': 'app.workers.tasks.cleanup_old_files_task',
        'schedule': 3600.0 * settings.CLEANUP_INTERVAL_HOURS,
    },
}
