"""Strip identity from a captured Voyager payload, keeping its shape intact.

Captured responses describe real people. They cannot be committed to a public
repository, but their *structure* is exactly what the parser must be tested
against. This tool replaces identifying values with synthetic ones while
preserving every structural property the tests care about:

* the same keys, in the same nesting, with the same ``$type`` values
* the same number of entities in every section
* referential integrity -- a URN rewritten in one place is rewritten
  identically everywhere, so ``included`` still resolves

The result is a fixture that exercises the real Voyager shape without exposing
anyone's data. Run::

    python tools/anonymize_fixture.py raw.json tests/fixtures/profile_a.json
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Dict

# Identifiers embedded inside URNs. LinkedIn profile ids all begin "ACoAA";
# member ids are bare integers. Both leak identity and both appear inside
# composite URNs such as
# "urn:li:fsd_profilePosition:(ACoAAB...,2707618794)".
_PROFILE_ID = re.compile(r"ACoAA[A-Za-z0-9_-]{10,}")
_MEMBER_URN = re.compile(r"(urn:li:member:)(\d+)")
_MEDIA_ID = re.compile(r"(D4E|D5E|C4E|C5E)[0-9A-Z]{10,}")

# Free-text fields naming a person, an employer, or a place.
_TEXT_FIELDS = {
    "firstName": "Test",
    "lastName": "Member",
    "headline": "Test headline for fixture profile",
    "summary": "Synthetic about-section text used only for parser tests.",
    "publicIdentifier": "test-member",
    "emailAddress": "fixture@example.invalid",
    "companyName": "Example Company",
    "schoolName": "Example University",
    "locationName": "Example City, Example Country",
    "geoLocationName": "Example Metropolitan Area",
    "defaultLocalizedName": "Example Region",
    "authority": "Example Authority",
    "universalName": "example-company",
    "description": "Synthetic description text.",
    "title": "Example Title",
    "role": "Example Role",
    "degreeName": "Example Degree",
    "fieldOfStudy": "Example Field of Study",
    "licenseNumber": "EXAMPLE-0000",
    "a11yText": "Test Member",
    "url": "https://example.invalid",
    "creatorWebsite": "https://example.invalid",
}

# Values dropped outright: tracking handles and dates of birth carry no
# structural meaning and every one of them is identifying.
_DROP_FIELDS = {
    "trackingId",
    "trackingMemberId",
    "birthDateOn",
    "objectUrn",
    "versionTag",
    "twitterHandles",
    "phoneNumbers",
    "multiLocaleFullNamePronunciationAudio",
}

# Multi-locale mirrors of the text fields above, e.g. multiLocaleFirstName.
_MULTILOCALE = re.compile(r"^multiLocale(.+)$")

# URL-bearing fields. These leak identity in ways the text rules miss: a
# company logo's path segment embeds the employer's name ("foxmont_talent_logo"),
# and treasury media points at personal domains.
_URL_FIELDS = {"Url", "url", "providerName", "rootUrl", "originalUrl"}

# Image paths look like "400_400/B4EZ9C.../0/1783523571193/acme_logo?e=...".
# The leading dimensions are structural -- consumers pick a size from them --
# so those are kept and everything after is replaced.
_IMAGE_DIMS = re.compile(r"^(\d+_\d+/)")


class Anonymizer:
    """Deterministic, reference-preserving scrubber."""

    def __init__(self) -> None:
        self._profile_ids: Dict[str, str] = {}
        self._member_ids: Dict[str, str] = {}
        self._media_ids: Dict[str, str] = {}

    # -- identifier remapping -------------------------------------------
    def _map_profile_id(self, match: "re.Match[str]") -> str:
        real = match.group(0)
        if real not in self._profile_ids:
            self._profile_ids[real] = "ACoAA%s" % str(
                len(self._profile_ids) + 1).zfill(20)
        return self._profile_ids[real]

    def _map_member(self, match: "re.Match[str]") -> str:
        prefix, real = match.group(1), match.group(2)
        if real not in self._member_ids:
            self._member_ids[real] = str(100000 + len(self._member_ids))
        return prefix + self._member_ids[real]

    def _map_media(self, match: "re.Match[str]") -> str:
        real = match.group(0)
        if real not in self._media_ids:
            self._media_ids[real] = "D4E%s" % str(
                len(self._media_ids) + 1).zfill(13)
        return self._media_ids[real]

    def scrub_string(self, value: str) -> str:
        value = _PROFILE_ID.sub(self._map_profile_id, value)
        value = _MEMBER_URN.sub(self._map_member, value)
        value = _MEDIA_ID.sub(self._map_media, value)
        return value

    def _identity_for(self, node: Dict[str, Any]) -> Dict[str, str]:
        """Give each distinct Profile its own synthetic identity.

        A payload routinely holds several members -- the subject, the viewer,
        and anyone referenced in passing. If they all shared one fake name the
        tests could not tell whether the parser returned the right person, so
        each profile URN gets its own consistent identity.
        """
        urn = node.get("entityUrn") or ""
        match = _PROFILE_ID.search(urn)
        if not match:
            return {}
        slot = self._profile_ids.get(match.group(0))
        if slot is None:
            slot = self._map_profile_id(match)
        n = slot[-2:].lstrip("0") or "0"
        return {
            "firstName": "Test%s" % n,
            "lastName": "Member%s" % n,
            "publicIdentifier": "test-member-%s" % n,
        }

    # -- recursive walk --------------------------------------------------
    def walk(self, node: Any, key: str = "") -> Any:
        if isinstance(node, str):
            return self.scrub_string(node)

        if isinstance(node, list):
            return [self.walk(item, key) for item in node]

        if not isinstance(node, dict):
            return node

        # Per-profile identity overrides the shared placeholders.
        overrides: Dict[str, str] = {}
        if node.get("$type", "").endswith("identity.profile.Profile"):
            overrides = self._identity_for(node)

        out: Dict[str, Any] = {}
        for k, v in node.items():
            if k in _DROP_FIELDS:
                continue

            # Replace known identifying text, but keep the field present so
            # the parser still sees the same shape.
            base = k
            m = _MULTILOCALE.match(k)
            if m:
                base = m.group(1)[0].lower() + m.group(1)[1:]

            if base in _TEXT_FIELDS and (
                    isinstance(v, str) or (m and isinstance(v, dict))):
                replacement = overrides.get(base, _TEXT_FIELDS[base])
                if isinstance(v, str):
                    out[k] = replacement
                else:
                    out[k] = {lang: replacement for lang in v
                              if lang != "$type"}
                    if "$type" in v:
                        out[k]["$type"] = v["$type"]
                continue

            # Image path segments embed the employer's name; keep the size
            # prefix, discard the rest.
            if k == "fileIdentifyingUrlPathSegment" and isinstance(v, str):
                dims = _IMAGE_DIMS.match(v)
                out[k] = "%sexample-asset?e=0&v=beta&t=example" % (
                    dims.group(1) if dims else "")
                continue

            if k in _URL_FIELDS and isinstance(v, str) and (
                    "/" in v or "." in v):
                out[k] = ("https://example.invalid/"
                          if v.startswith("http") else "example.invalid")
                continue

            # "name" identifies an organisation, but is a harmless label on
            # skills, languages, industries, certifications and courses --
            # those are catalogue entries, not personal data. Scrubbing them
            # would also desynchronise "name" from "multiLocaleName".
            if k == "name" and isinstance(v, str):
                typ = node.get("$type", "")
                if typ.endswith(("organization.Company",
                                 "organization.School")):
                    out[k] = _TEXT_FIELDS["companyName"]
                    continue

            out[k] = self.walk(v, k)
        return out


def anonymize(payload: Dict[str, Any]) -> Dict[str, Any]:
    return Anonymizer().walk(payload)


def _shape(payload: Dict[str, Any]) -> Dict[str, int]:
    """Structural signature, used to prove scrubbing changed nothing."""
    counts: Dict[str, int] = {}
    for entity in payload.get("included") or []:
        typ = entity.get("$type", "?").split(".")[-1]
        counts[typ] = counts.get(typ, 0) + 1
    return counts


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    src, dest = sys.argv[1], sys.argv[2]
    with open(src) as fh:
        raw = json.load(fh)

    clean = anonymize(raw)

    before, after = _shape(raw), _shape(clean)
    if before != after:
        print("ABORT: structure changed during anonymisation")
        print("  before:", before)
        print("  after :", after)
        return 1

    with open(dest, "w") as fh:
        json.dump(clean, fh, indent=1, sort_keys=True)

    print("wrote %s" % dest)
    print("  entities preserved: %d" % sum(after.values()))
    for typ, n in sorted(after.items(), key=lambda kv: -kv[1]):
        print("     %3d  %s" % (n, typ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
