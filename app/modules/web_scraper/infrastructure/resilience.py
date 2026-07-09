import logging
import asyncio
from functools import wraps
from typing import Callable, TypeVar, Any

from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log, RetryError,
)
from circuitbreaker import circuit, CircuitBreakerError

from app.core.logging import get_logger
log = get_logger(__name__)

# ── Sentinel exceptions ───────────────────────────────────────────────────────

class RateLimitError(Exception):
    """Raised on HTTP 429."""

class BlockedError(Exception):
    """Raised on HTTP 403 / CAPTCHA detection."""

class TransientNetworkError(Exception):
    """Raised on timeout / connection reset."""

class NotFoundError(Exception):
    """Raised on HTTP 404."""


# ── Retry decorator (wraps async callables) ───────────────────────────────────
RETRYABLE = (RateLimitError, TransientNetworkError)

def with_backoff(max_attempts: int = 5, base_wait: float = 2.0, max_wait: float = 60.0):
    """Exponential backoff; 403/BlockedError is NOT retried (opens circuit)."""
    def decorator(fn: Callable) -> Callable:
        @retry(
            reraise=True,
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=base_wait, max=max_wait),
            retry=retry_if_exception_type(RETRYABLE),
            before_sleep=before_sleep_log(log, logging.WARNING),   # Pillar 7 log on each sleep
        )
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            return await fn(*args, **kwargs)
        return wrapper
    return decorator


# ── Circuit breaker (per-domain state held by circuitbreaker lib) ─────────────
def with_circuit_breaker(failure_threshold: int = 5, recovery_timeout: int = 30):
    """Opens after `failure_threshold` successive failures; half-open after timeout."""
    def decorator(fn: Callable) -> Callable:
        # circuitbreaker is synchronous; we wrap the async fn in its own sync shell
        cb_fn = circuit(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=(RateLimitError, BlockedError, TransientNetworkError),
        )(fn)

        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await cb_fn(*args, **kwargs)
            except CircuitBreakerError as exc:
                log.error("circuit_open", extra={"reason": str(exc)})
                raise BlockedError(f"Circuit open: {exc}") from exc
        return wrapper
    return decorator