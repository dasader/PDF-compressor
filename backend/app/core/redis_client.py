"""공용 Redis 클라이언트 (lock, pubsub)"""
import redis
import redis.asyncio as aioredis
from app.core.config import settings

_CONNECT_KWARGS = dict(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD or None,
    decode_responses=False,
    socket_connect_timeout=5,
    socket_timeout=5,
)

#: 동기 경로용 (분산 락, 워커의 publish)
redis_client: redis.Redis = redis.Redis(**_CONNECT_KWARGS)

#: 이벤트 루프 위에서 도는 SSE 스트림용 — 동기 클라이언트를 쓰면 루프가 멈춘다
async_redis_client: aioredis.Redis = aioredis.Redis(**_CONNECT_KWARGS)
