"""FastAPI 메인 애플리케이션"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.logging import setup_logging
from app.models.database import engine, Base
from app.api import upload, jobs, health

# 로깅 설정
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 생명주기"""
    # 시작
    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} 시작")
    
    # 데이터베이스 테이블 생성 (이미 존재하는 경우 무시)
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("데이터베이스 초기화 완료")
    except Exception as e:
        logger.warning(f"데이터베이스 초기화 중 경고: {e}")
        logger.info("데이터베이스 테이블이 이미 존재합니다")

    # 복합 인덱스 보장 (기존 DB 대상)
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_status ON jobs (user_session, status)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_expires_created ON jobs (expires_at, created_at)"))
        logger.info("복합 인덱스 확인 완료")
    except Exception as e:
        logger.warning(f"인덱스 생성 중 경고: {e}")

    # 디렉토리 생성
    settings.ensure_directories()
    logger.info("디렉토리 생성 완료")
    
    yield
    
    # 종료
    logger.info("애플리케이션 종료")


# FastAPI 앱 생성
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="대용량 PDF 파일 압축 웹 애플리케이션",
    lifespan=lifespan
)

# 업로드 크기 제한 설정 (FastAPI는 기본적으로 큰 파일을 지원)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 라우터 등록
app.include_router(upload.router, prefix="/api", tags=["Upload"])
app.include_router(jobs.router, prefix="/api", tags=["Jobs"])
app.include_router(health.router, prefix="/api", tags=["Health"])


# 루트 엔드포인트
@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs"
    }


# 에러 핸들러
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """전역 예외 처리"""
    logger.error(f"처리되지 않은 예외: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": str(exc) if settings.ENVIRONMENT == "development" else "오류가 발생했습니다"
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.ENVIRONMENT == "development",
        workers=settings.WEB_CONCURRENCY
    )










