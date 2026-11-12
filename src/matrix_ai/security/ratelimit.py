"""Small in-process rate limiter for authenticated endpoints."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from ..errors import RateLimited


class RateLimiter:
    def __init__(self, limit_per_minute: int) -> None:
        self.limit = max(1, int(limit_per_minute))
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets[key]
            while bucket and now - bucket[0] > 60.0:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, int(60 - (now - bucket[0])))
                raise RateLimited(
                    "rate limit exceeded; slow down",
                    detail={"retry_after_seconds": retry_after, "limit_per_minute": self.limit},
                )
            bucket.append(now)

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return self.limit
            while bucket and now - bucket[0] > 60.0:
                bucket.popleft()
            return max(0, self.limit - len(bucket))

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
