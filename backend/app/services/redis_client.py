import logging

from app.config import settings

logger = logging.getLogger("invoiceflow")


class RedisClient:
    def __init__(self):
        self._client = None

    async def connect(self) -> bool:
        if not settings.redis_url:
            logger.info("Redis URL not set — using in-memory fallback")
            return False
        try:
            import redis.asyncio as redis

            self._client = redis.from_url(settings.redis_url, decode_responses=True)
            await self._client.ping()
            return True
        except Exception as exc:
            logger.warning("Redis unavailable: %s", exc)
            self._client = None
            return False

    async def ping(self) -> bool:
        if not self._client:
            return False
        try:
            await self._client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


redis_client = RedisClient()
