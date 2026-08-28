"""애플리케이션 설정"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    """애플리케이션 설정 클래스"""
    
    # 기본 설정
    APP_NAME: str = "PDF Compressor(made by mesmerized!)"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "production"
    
    # 서버 설정 (uvicorn이 WEB_CONCURRENCY를 환경변수로 직접 읽는다)
    WEB_CONCURRENCY: int = 2  # 4→2: 메모리 절약 (4GB 환경용)
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8000"
    
    # 업로드 설정
    MAX_UPLOAD_SIZE_MB: int = 512  # 2048→512: 메모리 절약 (4GB 환경용)
    MAX_FILES_PER_BATCH: int = 10  # 배치당 파일 수 상한
    UPLOAD_DIR: str = "/data/uploads"
    RESULT_DIR: str = "/data/results"
    TEMP_DIR: str = "/data/temp"
    
    # Redis 설정
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    
    # Celery 워커 설정
    WORKER_CONCURRENCY: int = 1  # 2→1: 동시 작업 1개만 (4GB 환경용)
    TASK_TIMEOUT_SECONDS: int = 900
    TASK_MAX_RETRIES: int = 3
    # POST /api/compress가 결과를 기다리는 상한. 넘으면 job_id를 돌려준다.
    SYNC_COMPRESS_TIMEOUT_SECONDS: int = 300
    
    # 파일 보관 설정
    RETENTION_HOURS: int = 24
    CLEANUP_INTERVAL_HOURS: int = 1
    
    # 보안 설정
    ENABLE_ANTIVIRUS: bool = False
    CLAMAV_HOST: str = "clamav"
    CLAMAV_PORT: int = 3310
    
    # 로깅
    LOG_LEVEL: str = "WARNING"  # INFO→WARNING: 로그 감소로 메모리 절약
    LOG_FORMAT: str = "json"
    
    # PDF 압축 설정
    ENABLE_ENGINE_FALLBACK: bool = True
    
    # 파일 해시 캐싱
    ENABLE_DEDUPLICATION: bool = True
    
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)
    
    @property
    def cors_origins_list(self) -> List[str]:
        """CORS origins를 리스트로 반환"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]
    
    @property
    def redis_url(self) -> str:
        """Redis 연결 URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    @property
    def max_upload_size_bytes(self) -> int:
        """최대 업로드 크기 (바이트)"""
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    
    def ensure_directories(self):
        """필요한 디렉토리 생성"""
        for dir_path in [self.UPLOAD_DIR, self.RESULT_DIR, self.TEMP_DIR]:
            Path(dir_path).mkdir(parents=True, exist_ok=True)


settings = Settings()
