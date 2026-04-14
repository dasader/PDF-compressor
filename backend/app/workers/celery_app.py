"""Celery 애플리케이션"""
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    'pdf_compressor',
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=['app.workers.tasks'],
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,

    task_track_started=True,
    task_time_limit=settings.TASK_TIMEOUT_SECONDS,
    task_soft_time_limit=max(30, settings.TASK_TIMEOUT_SECONDS - 60),

    task_acks_late=True,
    task_reject_on_worker_lost=True,

    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    worker_max_memory_per_child=1_200_000,  # KB = 1.2GB — auto-restart worker above this

    task_compression='gzip',

    result_expires=3600 * 12,  # 24h → 12h (Redis 용량 절감)

    broker_connection_retry_on_startup=True,
    broker_transport_options={'visibility_timeout': 3600},
)


















