"""A small in-process TTL cache.

Caching matters here for a specific reason: LinkedIn throttles sessions that
make too many requests, and the cheapest way to make fewer requests is to stop
asking twice for the same profile.

The cache key includes a hash of the session token, never the token itself,
and never the token in the clear. That is a privacy requirement rather than
tidiness: how much of a profile LinkedIn returns depends on the viewer's
relationship to that member, so a first-degree connection sees more than a
stranger does. Keying on the profile alone would let one caller's richer view
be served to another caller who is not entitled to it.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    """Bounded, time-limited cache. Not shared between processes."""

    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 512) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: Dict[str, Tuple[float, Any]] = {}

    @staticmethod
    def key(public_id: str, session_token: str) -> str:
        """Namespace an entry to one profile and one viewer.

        The token is hashed so nothing recoverable is held in memory.
        """
        digest = hashlib.sha256(session_token.encode("utf-8")).hexdigest()[:16]
        return f"{public_id}:{digest}"

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None

        expires_at, value = entry
        if time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        if len(self._store) >= self._max:
            self._evict_oldest()
        self._store[key] = (time.monotonic() + self._ttl, value)

    def _evict_oldest(self) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._store.items() if exp <= now]
        for key in expired:
            self._store.pop(key, None)

        # Still full after clearing expired entries: drop the nearest to
        # expiry so the cache stays bounded.
        if len(self._store) >= self._max and self._store:
            oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
            self._store.pop(oldest, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
