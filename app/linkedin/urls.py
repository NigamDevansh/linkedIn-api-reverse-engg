"""Turn whatever the caller sends into a LinkedIn public identifier.

The public identifier -- the ``charlieskipper`` in
``linkedin.com/in/charlieskipper`` -- is the only part Voyager needs. Callers
paste all sorts of things, so this module accepts the realistic variations and
rejects anything that is not a member profile.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote, urlparse

# A public identifier may hold unicode (names are not all ASCII), digits and
# hyphens. LinkedIn caps them at 100 characters.
_VALID_SLUG = re.compile(r"^[\w\-%.]{1,100}$", re.UNICODE)

# Paths that are LinkedIn URLs but are not member profiles. Sending one of
# these to the profile endpoint returns confusing errors, so name the problem.
_NON_PROFILE_PATHS = {
    "company": "a company page",
    "school": "a school page",
    "showcase": "a showcase page",
    "groups": "a group",
    "posts": "a post",
    "pulse": "an article",
    "jobs": "a job listing",
    "learning": "a LinkedIn Learning page",
    "events": "an event",
    "newsletters": "a newsletter",
}


class InvalidProfileURL(ValueError):
    """The input is not a LinkedIn member profile URL."""


def extract_public_id(value: str) -> str:
    """Return the public identifier for ``value``.

    Accepts a full profile URL, with or without scheme, ``www``, a locale
    subdomain (``in.linkedin.com``), a trailing slash, a locale suffix
    (``/in/slug/en``) or tracking query parameters. A bare identifier is
    accepted as-is.

    Raises :class:`InvalidProfileURL` when the input is empty, points at a
    non-member page, or carries no usable identifier.
    """
    if not value or not value.strip():
        raise InvalidProfileURL("No profile URL was provided.")

    raw = value.strip()

    # A bare slug: no scheme, no dots, no slashes.
    if "/" not in raw and "." not in raw:
        slug = unquote(raw)
        if not _VALID_SLUG.match(raw):
            raise InvalidProfileURL(f"{value!r} is not a valid profile id.")
        return slug

    # urlparse needs a scheme to find the host.
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    host = (parsed.netloc or "").lower().split(":")[0]

    if host and "linkedin.com" not in host:
        raise InvalidProfileURL(
            f"{host!r} is not a LinkedIn address."
        )

    segments = [seg for seg in parsed.path.split("/") if seg]
    if not segments:
        raise InvalidProfileURL(
            "The URL has no path. Expected linkedin.com/in/<profile-id>."
        )

    head = segments[0].lower()

    if head in _NON_PROFILE_PATHS:
        raise InvalidProfileURL(
            f"That link points at {_NON_PROFILE_PATHS[head]}, "
            "not a member profile."
        )

    # Sales Navigator links carry an internal id we cannot resolve.
    if head == "sales":
        raise InvalidProfileURL(
            "Sales Navigator links are not supported; "
            "use the public linkedin.com/in/<profile-id> URL."
        )

    if head != "in":
        raise InvalidProfileURL(
            "Expected a profile URL of the form linkedin.com/in/<profile-id>."
        )

    if len(segments) < 2:
        raise InvalidProfileURL("The URL is missing the profile identifier.")

    slug = segments[1]
    if not _VALID_SLUG.match(slug):
        raise InvalidProfileURL(f"{slug!r} is not a valid profile id.")

    return unquote(slug)


def profile_url(public_id: str) -> str:
    """Canonical public URL for a profile identifier."""
    return f"https://www.linkedin.com/in/{public_id}"


def normalize_li_at(value: Optional[str]) -> Optional[str]:
    """Reduce a pasted session cookie to the bare token.

    People paste ``li_at=AQED...``, a quoted value, or the token on its own.
    All three should work rather than failing on a stray character.
    """
    if not value:
        return None

    token = value.strip().strip('"').strip("'")

    # Pull it out of a longer cookie string if one was pasted. DevTools
    # renders some cookie values in quotes, so allow them around the token.
    match = re.search(r"""\bli_at\s*=\s*["']?([A-Za-z0-9_\-]+)""", token)
    if match:
        return match.group(1)

    token = token.strip().strip(";")
    return token or None
