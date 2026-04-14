"""작업 모델"""
from enum import Enum
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, Boolean, Enum as SQLEnum, Index
from app.models.database import Base


class JobStatus(str, Enum):
    """작업 상태"""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CompressionPreset(str, Enum):
    """압축 프리셋"""
    SCREEN = "screen"          # 최대 압축 (72 DPI)
    EBOOK = "ebook"            # 기본 (150 DPI)
    PRINTER = "printer"        # 균형 (300 DPI)
    PREPRESS = "prepress"      # 고품질 (300 DPI, 무손실)
    CUSTOM = "custom"          # 사용자 정의


class Job(Base):
    """작업 테이블"""
    __tablename__ = "jobs"
    
    # 기본 정보
    id = Column(String(36), primary_key=True, index=True)
    user_session = Column(String(100), index=True, nullable=True)
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
    eta_seconds = Column(Integer, nullable=True)
    
    # 압축 설정
    preset = Column(SQLEnum(CompressionPreset), default=CompressionPreset.EBOOK)
    engine = Column(String(50), default="ghostscript")
    custom_options = Column(Text, nullable=True)  # JSON
    
    # 메타데이터 옵션
    preserve_metadata = Column(Boolean, default=True)
    preserve_ocr = Column(Boolean, default=True)
    
    # 결과
    result_file = Column(String(500), nullable=True)
    result_url = Column(String(1000), nullable=True)
    compression_ratio = Column(Float, nullable=True)
    
    # 에러 정보
    error_message = Column(Text, nullable=True)
    error_details = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    
    # 타임스탬프
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True, index=True)
    
    # Celery
    celery_task_id = Column(String(100), nullable=True, index=True)

    __table_args__ = (
        Index("idx_user_status", "user_session", "status"),
        Index("idx_expires_created", "expires_at", "created_at"),
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

















