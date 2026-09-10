"""Runtime settings, read from the environment.

Note what is *not* here: there are no LinkedIn credentials. The service holds
no session of its own -- every request carries the caller's own ``li_at``,
which is used once and discarded. That is why this repository has no secrets
to keep out of it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # How long a fetched profile stays cached. Longer means fewer calls to
    # LinkedIn, which is the main defence against being throttled.
    cache_ttl_seconds: int = _int("CACHE_TTL_SECONDS", 3600)

    # Bounded so a long-running process cannot grow without limit.
    cache_max_entries: int = _int("CACHE_MAX_ENTRIES", 512)

    # Per-request timeout when talking to LinkedIn.
    upstream_timeout_seconds: int = _int("UPSTREAM_TIMEOUT_SECONDS", 20)

    log_level: str = os.environ.get("LOG_LEVEL", "INFO").upper()


settings = Settings()
