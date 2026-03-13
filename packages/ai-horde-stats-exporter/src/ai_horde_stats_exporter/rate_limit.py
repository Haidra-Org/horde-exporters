"""Thread-safe rate-limit state tracker for API requests."""

import threading
import time
from dataclasses import dataclass, field


@dataclass
class RateLimitState:
    """Thread-safe tracker for API rate-limit headers."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    limit: int = 0
    remaining: int = 0
    reset_epoch: float = 0.0
    retry_after: float = 0.0
    last_updated: float = 0.0

    def update_from_headers(self, headers: dict) -> None:
        with self._lock:
            self.limit = int(headers.get("x-ratelimit-limit", self.limit))
            self.remaining = int(headers.get("x-ratelimit-remaining", self.remaining))
            self.reset_epoch = float(headers.get("x-ratelimit-reset", self.reset_epoch))
            self.retry_after = float(headers.get("retry-after", self.retry_after))
            self.last_updated = time.time()

    def seconds_until_reset(self) -> float:
        with self._lock:
            return max(0.0, self.reset_epoch - time.time())

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "limit": self.limit,
                "remaining": self.remaining,
                "seconds_until_reset": max(0.0, self.reset_epoch - time.time()),
            }
