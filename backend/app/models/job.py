"""작업 모델"""
import os
from enum import Enum
from datetime import datetime, timedelta, timezone
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, Boolean, Enum as SQLEnum, Index
from app.core.config import settings
from app.models.database import Base


def utcnow() -> datetime:
    """naive UTC 현재 시각.

    SQLite는 DateTime 컬럼에서 tzinfo를 버리고 항상 naive를 돌려주므로,
    저장·비교를 전부 naive UTC로 통일해야 aware와 섞여 TypeError가 나지 않는다.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def expiry() -> datetime:
    """보관 기간이 끝나는 시각 — 성공/실패/취소 모두 이 값을 채워야 정리 대상이 된다."""
    return utcnow() + timedelta(hours=settings.RETENTION_HOURS)


class JobStatus(str, Enum):
    """작업 상태"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: 더 이상 상태가 바뀌지 않는 작업들 — SSE 종료/취소 거부/정리 대상 판단의 단일 기준.
#: JobStatus가 str Enum이라 "completed" 같은 문자열도 그대로 멤버십 검사가 된다.
TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})


class CompressionPreset(str, Enum):
    """압축 프리셋"""
    SCREEN = "screen"          # 최대 압축 (72 DPI)
    EBOOK = "ebook"            # 기본 (150 DPI)
    PRINTER = "printer"        # 균형 (300 DPI)
    PREPRESS = "prepress"      # 고품질 (300 DPI, 무손실)


class Job(Base):
    """작업 테이블"""
    __tablename__ = "jobs"

    # 기본 정보
    id = Column(String(36), primary_key=True)
    user_session = Column(String(100), nullable=True)
    filename = Column(String(500), nullable=False)
    original_filename = Column(String(500), nullable=False)

    # 파일 정보
    file_hash = Column(String(64), index=True, nullable=True)
    original_size = Column(Integer, nullable=False)
    compressed_size = Column(Integer, nullable=True)
    page_count = Column(Integer, nullable=True)
    image_count = Column(Integer, nullable=True)

    # 상태
    status = Column(SQLEnum(JobStatus), default=JobStatus.QUEUED, index=True)
    progress = Column(Float, default=0.0)

    # 압축 설정
    preset = Column(SQLEnum(CompressionPreset), default=CompressionPreset.EBOOK)
    engine = Column(String(50), default="ghostscript")

    # 메타데이터 옵션
    preserve_metadata = Column(Boolean, default=True)

    # 결과
    result_file = Column(String(500), nullable=True)
    compression_ratio = Column(Float, nullable=True)

    # 에러 정보
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # 타임스탬프
    created_at = Column(DateTime, default=utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    # Celery
    celery_task_id = Column(String(100), nullable=True)

    # 유일한 범위 스캔인 정리 작업(expires_at < cutoff AND status IN terminal)에 맞춘 인덱스 하나만 둔다
    __table_args__ = (
        Index("idx_expires_status", "expires_at", "status"),
    )

    @property
    def compression_percentage(self) -> float:
        """압축률 (퍼센트)"""
        if self.compression_ratio:
            return (1 - self.compression_ratio) * 100
        return 0.0

    @property
    def saved_bytes(self) -> int:
        """절약된 용량"""
        if self.compressed_size:
            return self.original_size - self.compressed_size
        return 0

    @property
    def upload_path(self) -> str | None:
        """업로드 원본 파일의 절대 경로"""
        return os.path.join(settings.UPLOAD_DIR, self.filename) if self.filename else None

    @property
    def result_path(self) -> str | None:
        """압축 결과 파일의 절대 경로"""
        return os.path.join(settings.RESULT_DIR, self.result_file) if self.result_file else None

    @property
    def result_exists(self) -> bool:
        """결과 파일이 실제로 디스크에 있는가"""
        return bool(self.result_file) and os.path.exists(self.result_path)

    @property
    def download_name(self) -> str:
        """사용자에게 내려줄 파일명"""
        return f"compressed_{self.original_filename}"
