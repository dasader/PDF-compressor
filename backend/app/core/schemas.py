"""Pydantic 스키마"""
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, field_serializer
from app.models.job import JobStatus


class JobResponse(BaseModel):
    """작업 응답"""
    id: str
    filename: str
    original_filename: str
    status: JobStatus
    progress: float

    # 파일 정보
    original_size: int
    compressed_size: Optional[int] = None
    compression_ratio: Optional[float] = None
    compression_percentage: Optional[float] = None
    saved_bytes: Optional[int] = None
    page_count: Optional[int] = None
    image_count: Optional[int] = None

    # 에러
    error_message: Optional[str] = None

    # 타임스탬프
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_serializer('created_at', 'started_at', 'completed_at', 'expires_at')
    def _stamp_utc(self, value: Optional[datetime]) -> Optional[datetime]:
        """DB의 naive UTC 값을 클라이언트가 오해하지 않도록 UTC로 표시한다."""
        return value.replace(tzinfo=timezone.utc) if value else value


class UploadFailure(BaseModel):
    """배치 중 실패한 파일 하나"""
    filename: str
    error: str


class UploadResponse(BaseModel):
    """업로드 응답 — 생성된 Job을 그대로 담아 클라이언트의 재조회를 없앤다"""
    jobs: list[JobResponse] = []
    failed: list[UploadFailure] = []
    message: str = "Files uploaded successfully"


class HealthResponse(BaseModel):
    """헬스체크 응답"""
    status: str
    version: str
    timestamp: datetime
    redis_connected: bool
