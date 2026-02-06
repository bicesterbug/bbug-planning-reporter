"""
Rate limiting middleware using Redis sliding window.

Implements [api-hardening:FR-003] - Rate limiting per API key
Implements [api-hardening:FR-004] - Configurable rate limits with default 60/min
Implements [api-hardening:NFR-002] - Prevent abuse (security)
Implements [api-hardening:NFR-004] - Performance under load

Implements test scenarios:
- [api-hardening:RateLimitMiddleware/TS-01] Under rate limit
- [api-hardening:RateLimitMiddleware/TS-02] Rate limit exceeded
- [api-hardening:RateLimitMiddleware/TS-03] Rate limit headers
- [api-hardening:RateLimitMiddleware/TS-04] Custom rate limit
- [api-hardening:RateLimitMiddleware/TS-05] Default rate limit
- [api-hardening:RateLimitMiddleware/TS-06] Window reset
- [api-hardening:RateLimitMiddleware/TS-07] Isolated per key
"""

import hashlib
import os
import time
from collections.abc import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger(__name__)

# Default configuration
DEFAULT_RATE_LIMIT = 60  # requests per window
DEFAULT_WINDOW_SECONDS = 60  # 1 minute window


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces per-API-key rate limits using Redis sliding window.

    Returns 429 with Retry-After header when limit exceeded.
    Adds rate limit headers to all responses.
    """

    def __init__(
        self,
        app,
        redis_client=None,
        rate_limit: int | None = None,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        get_redis: Callable | None = None,
    ) -> None:
        """
        Initialize rate limit middleware.

        Args:
            app: The ASGI application.
            redis_client: Redis client instance (optional).
            rate_limit: Max requests per window. Defaults to API_RATE_LIMIT env or 60.
            window_seconds: Window duration in seconds. Defaults to 60.
            get_redis: Async function to get Redis client (alternative to redis_client).
        """
        super().__init__(app)
        self._redis_client = redis_client
        self._get_redis = get_redis

        # Get rate limit from environment or use default
        env_limit = os.getenv("API_RATE_LIMIT")
        if rate_limit is not None:
            self.rate_limit = rate_limit
        elif env_limit:
            try:
                self.rate_limit = int(env_limit)
            except ValueError:
                logger.warning("Invalid API_RATE_LIMIT, using default", value=env_limit)
                self.rate_limit = DEFAULT_RATE_LIMIT
        else:
            self.rate_limit = DEFAULT_RATE_LIMIT

        self.window_seconds = window_seconds
        logger.info(
            "RateLimitMiddleware initialized",
            rate_limit=self.rate_limit,
            window_seconds=self.window_seconds,
        )

    async def _get_redis_client(self):
        """Get Redis client (lazy initialization)."""
        if self._redis_client:
            return self._redis_client
        if self._get_redis:
            return await self._get_redis()
        return None

    def _hash_key(self, api_key: str) -> str:
        """Hash API key for use in Redis keys (privacy)."""
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]

    def _rate_limit_key(self, key_hash: str) -> str:
        """Get Redis key for rate limit counter."""
        return f"rate_limit:{key_hash}"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request and check rate limit."""
        # Get API key from request state (set by AuthMiddleware)
        api_key = getattr(request.state, "api_key", None)

        # If no API key (e.g., health endpoint or auth disabled), skip rate limiting
        if not api_key:
            response = await call_next(request)
            return response

        redis = await self._get_redis_client()
        if not redis:
            # No Redis, skip rate limiting but log warning
            logger.warning("Rate limiting disabled: no Redis client")
            return await call_next(request)

        key_hash = self._hash_key(api_key)
        redis_key = self._rate_limit_key(key_hash)
        current_time = time.time()
        window_start = current_time - self.window_seconds

        try:
            # Sliding window implementation using sorted set
            # Score = timestamp, member = unique request ID
            pipe = redis.pipeline()

            # Remove expired entries
            await pipe.zremrangebyscore(redis_key, "-inf", window_start)

            # Count current entries
            await pipe.zcard(redis_key)

            results = await pipe.execute()
            current_count = results[1]

            if current_count >= self.rate_limit:
                # Rate limit exceeded
                # Calculate when the oldest request will expire
                oldest = await redis.zrange(redis_key, 0, 0, withscores=True)
                if oldest:
                    oldest_time = oldest[0][1]
                    retry_after = int(oldest_time + self.window_seconds - current_time) + 1
                else:
                    retry_after = self.window_seconds

                logger.warning(
                    "Rate limit exceeded",
                    api_key_hash=key_hash[:8] + "...",
                    current_count=current_count,
                    limit=self.rate_limit,
                )

                return self._rate_limited_response(
                    retry_after=retry_after,
                    limit=self.rate_limit,
                    remaining=0,
                    reset_time=int(current_time + retry_after),
                )

            # Add this request to the window
            request_id = f"{current_time}:{id(request)}"
            await redis.zadd(redis_key, {request_id: current_time})
            await redis.expire(redis_key, self.window_seconds + 10)  # Extra buffer for cleanup

            remaining = self.rate_limit - current_count - 1
            reset_time = int(current_time + self.window_seconds)

            # Process request
            response = await call_next(request)

            # Add rate limit headers
            response.headers["X-RateLimit-Limit"] = str(self.rate_limit)
            response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
            response.headers["X-RateLimit-Reset"] = str(reset_time)

            return response

        except Exception as e:
            # On Redis error, allow request but log error
            logger.error("Rate limiting error", error=str(e))
            return await call_next(request)

    def _rate_limited_response(
        self, retry_after: int, limit: int, remaining: int, reset_time: int
    ) -> JSONResponse:
        """Create a 429 Too Many Requests response."""
        response = JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please retry after the specified time.",
                    "details": {
                        "retry_after_seconds": retry_after,
                    },
                }
            },
        )
        response.headers["Retry-After"] = str(retry_after)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        return response
