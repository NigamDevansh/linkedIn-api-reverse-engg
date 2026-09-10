"""Authenticated Voyager client. No browser, no automation framework.

The whole mechanism, established by testing against the live API:

1. Seed a session with the caller's ``li_at`` cookie and nothing else.
2. Request ``/feed/`` once. LinkedIn replies with ``Set-Cookie: JSESSIONID``
   along with its routing cookies.
3. Use that ``JSESSIONID`` value verbatim as the ``csrf-token`` header --
   omitting it returns 403.
4. Call the profile endpoint.

Measured requirements (everything else was droppable):

* ``csrf-token``            -- 403 without it
* ``accept: ...normalized+json+2.1`` -- without it the response arrives in a
  different shape with no ``included`` array
* a browser-like ``user-agent`` -- curl's default gets redirected away

Credential handling: ``li_at`` is held only for the lifetime of one request,
is never written to disk, and is never logged. Exceptions raised here
deliberately carry no token material.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger(__name__)

BASE = "https://www.linkedin.com"
PROFILE_ENDPOINT = f"{BASE}/voyager/api/identity/dash/profiles"

# The decoration tells Voyager how much to inline. Without it the response
# carries the bare Profile and no sections at all. Versions 80 through 101 all
# returned identical payloads in testing, so the suffix is not a rotating
# hash -- unlike the GraphQL queryIds used elsewhere on the site.
DECORATION = (
    "com.linkedin.voyager.dash.deco.identity.profile."
    "FullProfileWithEntities-101"
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class LinkedInError(Exception):
    """Base class for upstream failures."""


class AuthenticationError(LinkedInError):
    """The li_at cookie is missing, expired, or was rejected."""


class RateLimited(LinkedInError):
    """LinkedIn is throttling this session."""


class ProfileNotFound(LinkedInError):
    """No such public identifier, or it is not visible to this session."""


class UpstreamError(LinkedInError):
    """LinkedIn answered in a way we could not use."""


class VoyagerClient:
    """One caller's LinkedIn session.

    Construct per request. The bootstrap costs one extra call, so a client
    that fetches several profiles should be reused rather than rebuilt --
    doubling traffic is the quickest route to being throttled.
    """

    def __init__(
        self,
        li_at: str,
        user_agent: Optional[str] = None,
        timeout: float = 20.0,
    ) -> None:
        if not li_at:
            raise AuthenticationError("A li_at session cookie is required.")

        self._li_at = li_at
        self._user_agent = user_agent or DEFAULT_USER_AGENT
        self._timeout = timeout
        self._csrf: Optional[str] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "VoyagerClient":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            headers={
                "user-agent": self._user_agent,
                "accept-language": "en-US,en;q=0.9",
            },
        )
        self._client.cookies.set("li_at", self._li_at, domain=".linkedin.com")
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- session ---------------------------------------------------------

    @staticmethod
    def _session_was_rejected(response: httpx.Response) -> bool:
        """True when LinkedIn is deleting the auth cookie we presented."""
        for header in response.headers.get_list("set-cookie"):
            name, _, rest = header.partition("=")
            if name.strip().lower() != "li_at":
                continue
            lowered = rest.lower()
            if "01-jan-1970" in lowered or "max-age=0" in lowered:
                return True
        return False

    async def _bootstrap(self) -> str:
        """Obtain a JSESSIONID from LinkedIn and derive the CSRF token.

        Asking the caller for JSESSIONID as well would be redundant: LinkedIn
        issues one for any valid session, so ``li_at`` alone is sufficient.
        """
        if self._csrf:
            return self._csrf

        assert self._client is not None
        try:
            response = await self._client.get(f"{BASE}/feed/")
        except httpx.RequestError as exc:
            raise UpstreamError(f"Could not reach LinkedIn: {exc}") from exc

        # An invalid token is answered by expiring it: LinkedIn returns
        # "Set-Cookie: li_at=...; Expires=Thu, 01-Jan-1970 ...; Max-Age=0",
        # which is the standard way to delete a cookie, then redirects to
        # itself. Each header is inspected individually -- checking the joined
        # string for "li_at" and "1970" separately would also fire when some
        # unrelated cookie is the one being cleared.
        if self._session_was_rejected(response):
            raise AuthenticationError(
                "LinkedIn rejected the li_at cookie. It has expired or was "
                "invalidated by a logout. Copy a fresh one from a browser "
                "where you are currently signed in."
            )

        jsessionid = self._client.cookies.get("JSESSIONID")
        if not jsessionid:
            raise AuthenticationError(
                "LinkedIn issued no session for that li_at cookie. It is "
                "most likely expired."
            )

        self._csrf = jsessionid.strip('"')
        return self._csrf

    # -- fetching --------------------------------------------------------

    async def fetch_profile(self, public_id: str) -> Dict[str, Any]:
        """Return the raw normalised Voyager payload for ``public_id``."""
        if self._client is None:
            raise RuntimeError(
                "VoyagerClient must be used as an async context manager.")

        csrf = await self._bootstrap()

        params = {
            "q": "memberIdentity",
            "memberIdentity": public_id,
            "decorationId": DECORATION,
        }
        headers = {
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "csrf-token": csrf,
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            "referer": f"{BASE}/in/{public_id}/",
        }

        try:
            response = await self._client.get(
                PROFILE_ENDPOINT, params=params, headers=headers)
        except httpx.RequestError as exc:
            raise UpstreamError(f"Could not reach LinkedIn: {exc}") from exc

        self._raise_for_status(response, public_id)

        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError(
                "LinkedIn returned a response that was not JSON."
            ) from exc

        if not isinstance(payload, dict) or "included" not in payload:
            raise UpstreamError(
                "LinkedIn returned an unexpected payload shape.")

        return payload

    @staticmethod
    def _raise_for_status(response: httpx.Response, public_id: str) -> None:
        status = response.status_code

        if status == 200:
            return

        if status in (401, 403):
            raise AuthenticationError(
                "LinkedIn refused the session (HTTP %d). The li_at cookie is "
                "expired or invalid." % status
            )

        if status == 404:
            raise ProfileNotFound(f"No LinkedIn profile named {public_id!r}.")

        if status == 429:
            raise RateLimited(
                "LinkedIn is rate limiting this session. Wait before retrying."
            )

        # A redirect back to the same URL is LinkedIn's soft block: following
        # it loops forever, so it is reported rather than chased.
        if status in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if location.rstrip("/") == str(response.url).rstrip("/"):
                raise RateLimited(
                    "LinkedIn is soft-blocking this session (it redirects the "
                    "request to itself). This usually clears within a few "
                    "hours. Slow down, or use a different session."
                )
            raise AuthenticationError(
                "LinkedIn redirected the request, which usually means the "
                "session is not signed in."
            )

        if 500 <= status < 600:
            raise UpstreamError(f"LinkedIn returned HTTP {status}.")

        raise UpstreamError(f"Unexpected response from LinkedIn: {status}.")


async def fetch_profile(
    public_id: str,
    li_at: str,
    user_agent: Optional[str] = None,
    retries: int = 1,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """Convenience wrapper: open a session, fetch once, close.

    Retries only transient upstream failures. Authentication problems, missing
    profiles and rate limits are all final -- retrying them makes throttling
    worse rather than better.
    """
    last: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            async with VoyagerClient(li_at, user_agent, timeout) as client:
                return await client.fetch_profile(public_id)
        except (AuthenticationError, ProfileNotFound, RateLimited):
            raise
        except UpstreamError as exc:
            last = exc
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))

    raise last if last else UpstreamError("Profile fetch failed.")
