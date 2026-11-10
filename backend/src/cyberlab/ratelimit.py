"""Per-client rate limiting with a token bucket.

Each client has a bucket of ``burst`` tokens that refills at ``per_minute / 60`` tokens per
second; every request spends one. Short bursts are fine, a sustained flood gets
``429 Too Many Requests`` with ``Retry-After``.

Clients are identified by a hash of their bearer token, or by IP address when anonymous.
State is in memory, so with several server processes each enforces its own share; a shared
store (e.g. Redis) would be needed to enforce one global limit.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_CLIENTS = 10_000
EXEMPT_PREFIXES = ("/healthz", "/readyz", "/static/", "/terminal")


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    per_minute: int
    burst: int
    clock: Callable[[], float] = field(default=time.monotonic)
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0

    def check(self, key: str) -> float:
        """Spend one token. Returns 0 if allowed, else seconds until the next token."""
        now = self.clock()
        rate = self.per_minute / 60.0
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= MAX_CLIENTS:
                self._prune(now, rate)
            bucket = self._buckets[key] = _Bucket(float(self.burst), now)
        bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * rate)
        bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0.0
        return (1 - bucket.tokens) / rate

    def _prune(self, now: float, rate: float) -> None:
        """Forget clients whose buckets have refilled completely (they're idle)."""
        full = [
            k for k, b in self._buckets.items() if b.tokens + (now - b.updated) * rate >= self.burst
        ]
        for k in full:
            del self._buckets[k]
        if len(self._buckets) >= MAX_CLIENTS:  # still full: drop the oldest half
            for k, _ in sorted(self._buckets.items(), key=lambda kv: kv[1].updated)[
                : MAX_CLIENTS // 2
            ]:
                del self._buckets[k]


def client_key(authorization: str | None, ip: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return "t:" + hashlib.sha256(authorization[7:].strip().encode()).hexdigest()[:24]
    return f"ip:{ip or 'unknown'}"


def retry_after_header(wait: float) -> str:
    return str(max(1, math.ceil(wait)))
