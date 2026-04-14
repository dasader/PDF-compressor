"""공용 Redis 클라이언트 (lock, pubsub)"""
import redis
from app.core.config import settings


def _build_client() -> redis.Redis:
    return redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


redis_client: redis.Redis = _build_client()
