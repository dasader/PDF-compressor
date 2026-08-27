"""헬스체크 API"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.redis_client import redis_client
from app.core.schemas import HealthResponse

router = APIRouter()
logger = logging.getLogger(__name__)


def _redis_ok() -> bool:
    try:
        redis_client.ping()
        return True
    except Exception as e:
        logger.error(f"Redis 연결 실패: {e}")
        return False


# 두 엔드포인트 모두 sync def — Starlette가 threadpool로 돌려 blocking ping이 이벤트 루프를 막지 않는다
@router.get("/healthz", response_model=HealthResponse)
def health_check():
    """헬스체크 엔드포인트 (Redis 연결만 확인, 워커는 선택적)"""
    connected = _redis_ok()
    return HealthResponse(
        status="healthy" if connected else "degraded",
        version=settings.APP_VERSION,
        timestamp=datetime.now(timezone.utc),
        redis_connected=connected,
    )


@router.get("/readyz")
def readiness_check():
    """준비 상태 확인"""
    if not _redis_ok():
        raise HTTPException(status_code=503, detail="서비스 준비되지 않음: Redis 연결 실패")
    return {"status": "ready"}
