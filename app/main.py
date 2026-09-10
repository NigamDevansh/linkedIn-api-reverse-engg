"""LinkedIn Profile API.

Accepts a LinkedIn profile URL and returns that profile as structured JSON,
by calling LinkedIn's internal Voyager API directly over HTTP. No browser,
no headless automation.

Credentials belong to the caller. Requests run against the caller's own
LinkedIn session and their own rate limit, and the token is held only for the
duration of one request -- never stored, never written to a log.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app import models
from app.cache import TTLCache
from app.config import settings
from app.linkedin import client as voyager
from app.linkedin.denormalize import denormalize
from app.linkedin.mapper import to_profile
from app.linkedin.urls import InvalidProfileURL, extract_public_id, normalize_li_at

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("linkedin-api")

DESCRIPTION = """
Returns a LinkedIn profile as structured JSON.

### How it works

This service calls LinkedIn's internal **Voyager** API directly
(`/voyager/api/identity/dash/profiles`). There is no browser and no headless
automation anywhere in the request path.

### You must supply your own LinkedIn session

Requests are made **on your behalf**, using your session and your rate limit.

**Getting your `li_at` cookie (Chrome):**

1. Open [linkedin.com](https://www.linkedin.com) and make sure you are signed in
2. Press `F12` (or `Cmd+Option+I` on a Mac) to open DevTools
3. Open the **Application** tab
4. In the left sidebar choose **Storage → Cookies → https://www.linkedin.com**
5. Type `li_at` into the filter box
6. Double-click the **Value** cell and copy it

For `user_agent`, paste the browser's own user agent — visit
`chrome://version` and copy the "User Agent" line. Use the same browser the
cookie came from.

### Privacy

Your `li_at` is used for the single request and then discarded. It is never
logged, never written to disk, and never returned in a response.
"""

app = FastAPI(
    title="LinkedIn Profile API",
    description=DESCRIPTION,
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

cache = TTLCache(
    ttl_seconds=settings.cache_ttl_seconds,
    max_entries=settings.cache_max_entries,
)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "cached_profiles": len(cache)}


@app.post(
    "/api/v1/profile",
    response_model=models.ProfileResponse,
    responses={
        400: {"model": models.ErrorResponse},
        401: {"model": models.ErrorResponse},
        404: {"model": models.ErrorResponse},
        429: {"model": models.ErrorResponse},
        502: {"model": models.ErrorResponse},
    },
    tags=["profile"],
    summary="Fetch a LinkedIn profile as structured JSON",
)
async def get_profile(payload: models.ProfileRequest) -> models.ProfileResponse:
    public_id = extract_public_id(payload.profile_url)

    li_at = normalize_li_at(payload.li_at)
    if not li_at:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "invalid_credentials",
            "The li_at value could not be read.",
            "Copy the cookie's Value field exactly.",
        )

    key = cache.key(public_id, li_at)
    cached = cache.get(key)
    if cached is not None:
        return models.ProfileResponse(
            profile=cached,
            fetched_at=datetime.now(timezone.utc),
            cached=True,
        )

    raw = await voyager.fetch_profile(
        public_id,
        li_at=li_at,
        user_agent=payload.user_agent,
        timeout=settings.upstream_timeout_seconds,
    )

    denormalized = denormalize(raw, public_id=public_id)
    if denormalized is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "profile_unavailable",
            f"LinkedIn returned no profile for {public_id!r}.",
            "The account may be deleted, restricted, or wholly outside your "
            "network.",
        )

    profile = to_profile(denormalized)
    cache.set(key, profile)

    return models.ProfileResponse(
        profile=profile,
        fetched_at=datetime.now(timezone.utc),
        cached=False,
    )


# -- error handling -----------------------------------------------------

class _APIError(Exception):
    def __init__(self, status_code: int, error: str, detail: str,
                 hint: str = None) -> None:
        self.status_code = status_code
        self.error = error
        self.detail = detail
        self.hint = hint


def _error(status_code: int, error: str, detail: str,
           hint: str = None) -> _APIError:
    return _APIError(status_code, error, detail, hint)


@app.exception_handler(_APIError)
async def _handle_api_error(request: Request, exc: _APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=models.ErrorResponse(
            error=exc.error, detail=exc.detail, hint=exc.hint
        ).model_dump(exclude_none=True),
    )


@app.exception_handler(InvalidProfileURL)
async def _handle_bad_url(request: Request,
                          exc: InvalidProfileURL) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=models.ErrorResponse(
            error="invalid_profile_url",
            detail=str(exc),
            hint="Expected https://www.linkedin.com/in/<profile-id>",
        ).model_dump(exclude_none=True),
    )


def _upstream_handler(error_name: str, status_code: int, hint: str = None):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        # Log the class only. Messages here never carry token material, but
        # keeping credentials out of logs is worth being deliberate about.
        log.warning("upstream failure: %s", type(exc).__name__)
        return JSONResponse(
            status_code=status_code,
            content=models.ErrorResponse(
                error=error_name, detail=str(exc), hint=hint
            ).model_dump(exclude_none=True),
        )
    return handler


app.add_exception_handler(
    voyager.AuthenticationError,
    _upstream_handler(
        "authentication_failed", status.HTTP_401_UNAUTHORIZED,
        "Copy a fresh li_at from a browser where you are currently signed in."),
)
app.add_exception_handler(
    voyager.ProfileNotFound,
    _upstream_handler("profile_not_found", status.HTTP_404_NOT_FOUND),
)
app.add_exception_handler(
    voyager.RateLimited,
    _upstream_handler(
        "rate_limited", status.HTTP_429_TOO_MANY_REQUESTS,
        "LinkedIn throttles sessions that request too much too quickly. "
        "Wait, then retry."),
)
app.add_exception_handler(
    voyager.UpstreamError,
    _upstream_handler("upstream_error", status.HTTP_502_BAD_GATEWAY),
)
