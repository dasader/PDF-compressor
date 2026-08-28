"""FastAPI 메인 애플리케이션"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.logging import setup_logging
from app.init_db import init_db
from app.api import compress, upload, jobs, health

# 로깅 설정
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """애플리케이션 생명주기"""
    # 시작
    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} 시작")
    
    init_db()
    settings.ensure_directories()

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
app.include_router(compress.router, prefix="/api", tags=["Compress"])
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
