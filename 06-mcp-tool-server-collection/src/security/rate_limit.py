"""Per-token rate limiting with a sliding 1-hour window.

In-memory deques, threadsafe. Production deployments would back this with
Redis (so multiple replicas share the limit), but the interface is the same:
    rl.check_and_record(principal_id, limit_per_hour)
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

WINDOW_S = 3600.0


class RateLimitError(Exception):
    pass


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def check_and_record(self, key: str, limit_per_hour: int) -> None:
        if limit_per_hour <= 0:
            return
        now = time.monotonic()
        cutoff = now - WINDOW_S
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit_per_hour:
                raise RateLimitError(
                    f"rate limit exceeded for {key}: {limit_per_hour} req/hr"
                )
            bucket.append(now)

    def remaining(self, key: str, limit_per_hour: int) -> int:
        now = time.monotonic()
        cutoff = now - WINDOW_S
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            return max(0, limit_per_hour - len(bucket))
