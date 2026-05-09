"""
LLM call resilience: exponential backoff on rate limits (Fortress AI copy).
"""

from __future__ import annotations

import functools
import logging
import random
import threading
import time
from collections import deque
from typing import Any, Callable, TypeVar

logger = logging.getLogger("llm_resilience")

F = TypeVar("F", bound=Callable[..., Any])


def _is_rate_limit(exc: BaseException) -> bool:
    s = f"{type(exc).__name__} {exc}".upper()
    return "429" in s or ("RATE" in s and "LIMIT" in s)


def exponential_backoff_retry(
    *,
    initial_wait: float = 2.0,
    multiplier: float = 2.0,
    max_retries: int = 4,
    max_wait: float = 30.0,
) -> Callable[[F], F]:
    """Decorator for callables that may hit provider rate limits."""

    def deco(fn: F) -> F:
        @functools.wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            wait = float(initial_wait)
            last_exc: BaseException | None = None
            for attempt in range(int(max_retries) + 1):
                try:
                    return fn(*args, **kwargs)
                except BaseException as e:
                    last_exc = e
                    if not _is_rate_limit(e) and attempt == 0:
                        raise
                    if attempt >= int(max_retries):
                        logger.exception(
                            "llm_resilience: exhausted retries for %s", getattr(fn, "__name__", fn)
                        )
                        raise
                    jitter = random.uniform(0, 0.25 * wait)
                    sleep_s = min(float(max_wait), wait + jitter)
                    logger.warning(
                        "llm_resilience: retry %s attempt=%s sleep=%.2fs err=%s",
                        getattr(fn, "__name__", fn),
                        attempt + 1,
                        sleep_s,
                        e,
                    )
                    time.sleep(sleep_s)
                    wait = min(float(max_wait), wait * float(multiplier))
            assert last_exc is not None
            raise last_exc

        return wrapped  # type: ignore[return-value]

    return deco


class TokenBucketLimiter:
    def __init__(self, *, rate: float, per_seconds: float = 60.0) -> None:
        self.rate = float(rate)
        self.per_seconds = float(per_seconds)
        self._tokens = float(rate)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self.rate, self._tokens + elapsed * (self.rate / self.per_seconds))
            self._last = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


class AsyncLLMQueue:
    def __init__(self, *, limiter: TokenBucketLimiter, label: str = "default") -> None:
        self.limiter = limiter
        self.label = label
        self._recent_blocked = deque(maxlen=50)

    def schedule_or_warn(self, fn: Callable[[], Any]) -> Any | None:
        if self.limiter.acquire(1.0):
            return fn()
        logger.warning("llm_queue_overflow label=%s", self.label)
        self._recent_blocked.append(time.time())
        return None
